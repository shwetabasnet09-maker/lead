"""Replace service-name search keywords with phrases people use when asking for care.

Service names ("personal care", "physiotherapy", "sil") mostly matched provider
ads and job posts. Every existing "find posts with this" keyword is removed and
replaced with the list in leads.keyword_defaults; the skip list is extended.
"""

from django.db import migrations

from leads.keyword_defaults import NEGATIVE_KEYWORDS, POSITIVE_KEYWORDS


def replace_keywords(apps, schema_editor):
    Keyword = apps.get_model("leads", "Keyword")
    SearchProfile = apps.get_model("leads", "SearchProfile")
    ServiceCategory = apps.get_model("leads", "ServiceCategory")

    categories = {category.name: category for category in ServiceCategory.objects.all()}
    for profile in SearchProfile.objects.all():
        Keyword.objects.filter(profile=profile, kind="positive").delete()
        for category_name, terms in POSITIVE_KEYWORDS.items():
            for term in terms:
                Keyword.objects.create(
                    profile=profile,
                    term=term,
                    kind="positive",
                    category=categories.get(category_name) if category_name else None,
                    generated=True,
                )
        for term in NEGATIVE_KEYWORDS:
            Keyword.objects.get_or_create(profile=profile, term=term, kind="negative")


class Migration(migrations.Migration):
    dependencies = [("leads", "0004_gemini_relevance_and_weekly_freshness")]

    operations = [migrations.RunPython(replace_keywords, migrations.RunPython.noop)]
