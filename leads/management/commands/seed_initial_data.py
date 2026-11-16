from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from leads.keyword_defaults import NEGATIVE_KEYWORDS, POSITIVE_KEYWORDS
from leads.models import Keyword, Membership, Organisation, SearchProfile, ServiceCategory, Source


CATEGORIES = {
    "Home care / in-home support": [
        "home care",
        "homecare",
        "in-home care",
        "in home support",
        "assistance with daily living",
        "daily living assistance",
    ],
    "Personal care": ["personal care", "daily personal activities", "nursing and personal care"],
    "Support worker": ["support worker", "disability support worker"],
    "Community participation": ["community participation", "social participation"],
    "Life skills development": ["life skills", "life skills development", "independent living skills"],
    "Domestic assistance / cleaning": [
        "domestic assistance",
        "ndis cleaning",
        "home cleaning",
        "house cleaning",
        "home help",
        "house cleaning / home help",
        "cleaner",
    ],
    "Nursing": ["home care nurse", "community nursing", "ndis nurse", "nursing and personal care"],
    "Day programs / day care": ["day program", "day care"],
    "Respite / Short-Term Accommodation": ["respite", "short term accommodation", "sta"],
    "Supported Independent Living": ["supported independent living", "sil"],
    "Specialist Disability Accommodation": ["specialist disability accommodation", "sda"],
    "Transport": ["ndis transport", "accessible transport", "travel and transport"],
    "Meal preparation": ["meal preparation", "meal support"],
    "Occupational therapy": ["occupational therapist", "occupational therapy", "ot"],
    "Physiotherapy": ["physiotherapy", "physiotherapist"],
    "Speech therapy": ["speech therapy", "speech pathologist"],
    "Psychology / counselling": ["psychologist", "psychology", "counselling"],
    "Behaviour support": ["behaviour support", "positive behaviour support"],
    "Therapy assistant": ["therapy assistant", "allied health assistant"],
    "Support coordination": ["support coordinator", "support coordination"],
    "Plan management": ["plan manager", "plan management"],
    "Home modifications": ["home modification", "accessible home modification"],
    "Other service request": [],
}

# Free official APIs. Bluesky runs one search per service through {service} (see
# leads.api_sources.SERVICE_PHRASES). Brave groups the services with OR and avoids
# {location}: 6 queries x 4 runs a day stays inside the ~1,000 free searches a month.
API_SOURCES = [
    (
        "Mastodon public hashtags",
        Source.SourceType.MASTODON,
        "https://mastodon.social/",
        {"hashtags": ["ndis", "ndisaustralia", "ndisparticipant", "disabilitysupport"]},
    ),
    (
        "Bluesky post search",
        Source.SourceType.BLUESKY,
        "https://bsky.social/",
        {"queries": ['ndis "{service}"', "ndis looking for", "ndis need support worker", "ndis recommend provider"]},
    ),
    (
        "Brave web search",
        Source.SourceType.BRAVE,
        "https://api.search.brave.com/",
        {
            "queries": [
                'NDIS "looking for" "supported independent living" OR SIL OR "short term accommodation" OR respite Victoria',
                'NDIS "looking for" OR "need" "support coordinator" OR "occupational therapist" OR "behaviour support" Melbourne',
                'NDIS "looking for" OR "recommend" "therapy assistant" OR "life skills" OR "community participation" Victoria',
                'NDIS "looking for" OR "need" "personal care" OR "daily living" OR cleaner OR "home help" Melbourne',
                'NDIS "looking for" OR "need" transport OR "support worker" Melbourne',
                'site:reddit.com NDIS "looking for" OR "recommend" Melbourne OR Victoria',
            ]
        },
    ),
]


class Command(BaseCommand):
    help = "Create the default organisation, taxonomy, Melbourne search profile, and disabled example sources."

    def add_arguments(self, parser):
        parser.add_argument("--username", default="admin")
        parser.add_argument(
            "--refresh-sources",
            action="store_true",
            help="Overwrite the configuration of existing API sources with the default queries.",
        )

    def handle(self, *args, **options):
        organisation, _ = Organisation.objects.get_or_create(slug="default", defaults={"name": "NDIS Lead Finder"})
        profile, _ = SearchProfile.objects.get_or_create(
            organisation=organisation,
            name="Melbourne and Victoria",
            defaults={"states": "VIC", "locations": "Melbourne\nDandenong\nGeelong", "radius_km": 50},
        )
        categories = {}
        for name, synonyms in CATEGORIES.items():
            category, created = ServiceCategory.objects.get_or_create(
                name=name,
                defaults={"slug": slugify(name)[:50], "synonyms": "\n".join(synonyms)},
            )
            categories[name] = category
            if not created:
                existing = [term.strip() for term in category.synonyms.splitlines() if term.strip()]
                merged = list(dict.fromkeys([*existing, *synonyms]))
                if merged != existing:
                    category.synonyms = "\n".join(merged)
                    category.save(update_fields=["synonyms", "updated_at"])
        for category_name, terms in POSITIVE_KEYWORDS.items():
            for term in terms:
                Keyword.objects.get_or_create(
                    profile=profile,
                    term=term,
                    kind=Keyword.Kind.POSITIVE,
                    defaults={"category": categories.get(category_name), "generated": True},
                )
        for term in NEGATIVE_KEYWORDS:
            Keyword.objects.get_or_create(profile=profile, term=term, kind=Keyword.Kind.NEGATIVE)
        for name, url in [
            ("MyCareSpace reference", "https://mycarespace.com.au/"),
            ("Clickability reference", "https://clickability.com.au/"),
            ("Karista reference", "https://www.karista.com.au/"),
            ("MySolas reference", "https://www.mysolas.com.au/"),
        ]:
            Source.objects.get_or_create(
                name=name,
                defaults={"source_type": Source.SourceType.HTML, "base_url": url, "search_url_template": url, "enabled": False},
            )
        for name, source_type, url, config in API_SOURCES:
            source, created = Source.objects.get_or_create(
                name=name,
                defaults={"source_type": source_type, "base_url": url, "search_url_template": url, "selectors": config, "enabled": False},
            )
            if not created and options["refresh_sources"] and source.selectors != config:
                source.selectors = config
                source.save(update_fields=["selectors", "updated_at"])
                self.stdout.write(f"Refreshed queries for {name}.")
        user = User.objects.filter(username=options["username"]).first()
        if user:
            Membership.objects.get_or_create(organisation=organisation, user=user, defaults={"role": Membership.Role.OWNER})
        self.stdout.write(self.style.SUCCESS("Initial taxonomy and safe disabled reference sources created."))
