"""Add the Selenium web search source, switched off.

Approve it in Settings > Sources once its terms have been reviewed.
"""

from django.db import migrations

from leads.keyword_defaults import WEB_SEARCH_SOURCE, WEB_SEARCH_SOURCE_NAME


def add_web_search(apps, schema_editor):
    Source = apps.get_model("leads", "Source")
    Source.objects.get_or_create(
        name=WEB_SEARCH_SOURCE_NAME,
        defaults={
            "source_type": "selenium",
            "base_url": WEB_SEARCH_SOURCE["base_url"],
            "search_url_template": WEB_SEARCH_SOURCE["search_url_template"],
            "selectors": WEB_SEARCH_SOURCE["selectors"],
            "robots_reviewed": True,
            "request_delay_seconds": 5,
            "enabled": False,
        },
    )


class Migration(migrations.Migration):
    dependencies = [("leads", "0006_target_business_services")]

    operations = [migrations.RunPython(add_web_search, migrations.RunPython.noop)]
