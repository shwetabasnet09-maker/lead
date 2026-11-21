"""Aim keywords and Bluesky searches at the twelve services the business offers.

Every "find posts with this" keyword is replaced with request phrases for those
services, the skip list is extended, and the Bluesky source searches for people
asking ("looking for", "need", "recommend") instead of any post naming a service.
"""

from django.db import migrations

from leads.keyword_defaults import BLUESKY_QUERIES, NEGATIVE_KEYWORDS, POSITIVE_KEYWORDS


def target_business_services(apps, schema_editor):
    Keyword = apps.get_model("leads", "Keyword")
    SearchProfile = apps.get_model("leads", "SearchProfile")
    ServiceCategory = apps.get_model("leads", "ServiceCategory")
    Source = apps.get_model("leads", "Source")

    categories = {category.name: category for category in ServiceCategory.objects.all()}
    for profile in SearchProfile.objects.all():
        Keyword.objects.filter(profile=profile, kind="positive").delete()
        for category_name, terms in POSITIVE_KEYWORDS.items():
            for term in terms:
                Keyword.objects.get_or_create(
                    profile=profile,
                    term=term,
                    kind="positive",
                    defaults={"category": categories.get(category_name) if category_name else None, "generated": True},
                )
        for term in NEGATIVE_KEYWORDS:
            Keyword.objects.get_or_create(profile=profile, term=term, kind="negative")

    for source in Source.objects.filter(source_type="bluesky"):
        source.selectors = {**(source.selectors or {}), "queries": BLUESKY_QUERIES}
        source.save(update_fields=["selectors"])


class Migration(migrations.Migration):
    dependencies = [("leads", "0005_replace_keywords_with_need_phrases")]

    operations = [migrations.RunPython(target_business_services, migrations.RunPython.noop)]
