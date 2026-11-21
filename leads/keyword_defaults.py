"""What the business offers, and the default search keywords built from it.

Keywords are written the way participants, families and support coordinators
ask for a service ("looking for a support coordinator", "need respite for my
son"), not as bare service names, which mostly match provider ads and job posts.
Category ``None`` means the keyword applies to any service.
"""

# The services on the business website, as people see them.
BUSINESS_SERVICES = [
    "Supported Independent Living (SIL)",
    "Short-Term Accommodation (STA)",
    "Assistance with Daily Living",
    "Community Participation",
    "Life Skills Development",
    "Travel and Transport",
    "Support Coordination",
    "Nursing and Personal Care",
    "House Cleaning / Home Help",
    "Occupational Therapy",
    "Behaviour Support",
    "Therapy Assistant",
]

POSITIVE_KEYWORDS = {
    None: [
        "looking for an ndis provider",
        "looking for a provider",
        "recommend an ndis provider",
        "ndis provider recommendations",
        "can anyone recommend",
        "can't find a provider",
        "need ndis support",
        "just got my ndis plan",
        "new ndis plan",
        "my son's ndis",
        "my daughter's ndis",
    ],
    "Supported Independent Living": [
        "looking for sil",
        "need sil",
        "sil placement",
        "looking for supported independent living",
        "need supported independent living",
        "supported accommodation for my",
    ],
    "Respite / Short-Term Accommodation": [
        "looking for respite",
        "need respite",
        "respite for my",
        "looking for sta",
        "need sta",
        "short term accommodation for",
        "need a break from caring",
    ],
    "Home care / in-home support": [
        "help with daily living",
        "need help at home",
        "looking for a support worker",
        "need a support worker",
        "support worker for my",
        "carer for my",
    ],
    "Community participation": [
        "someone to take my son",
        "someone to take my daughter",
        "community access support",
        "help getting out in the community",
        "social support for my",
    ],
    "Life skills development": [
        "life skills support",
        "help to live independently",
        "independent living skills for my",
        "help learning to cook",
        "help with budgeting",
    ],
    "Transport": [
        "need transport to",
        "need a lift to",
        "ndis transport needed",
        "transport to appointments",
        "driver for my",
    ],
    "Support coordination": [
        "looking for a support coordinator",
        "need a support coordinator",
        "recommend a support coordinator",
        "new support coordinator",
        "change support coordinator",
    ],
    "Personal care": [
        "help with showering",
        "help showering",
        "help getting dressed",
        "help with personal care",
        "personal care for my",
    ],
    "Nursing": [
        "need a nurse at home",
        "nurse for my",
    ],
    "Domestic assistance / cleaning": [
        "need a cleaner",
        "looking for a cleaner",
        "ndis cleaner",
        "help with housework",
        "home help needed",
    ],
    "Occupational therapy": [
        "looking for an ot",
        "need an ot",
        "recommend an ot",
        "looking for an occupational therapist",
        "need an occupational therapist",
        "ot assessment for",
    ],
    "Behaviour support": [
        "looking for a behaviour support",
        "need behaviour support",
        "behaviour support for my",
        "recommend a behaviour support",
        "need a bsp",
    ],
    "Therapy assistant": [
        "looking for a therapy assistant",
        "need a therapy assistant",
        "therapy assistant for my",
    ],
}

NEGATIVE_KEYWORDS = [
    "job",
    "vacancy",
    "hiring",
    "course",
    "training",
    "we provide",
    "our services",
    "we are hiring",
    "now hiring",
    "apply now",
    "job opportunity",
    "position available",
    "salary",
    "certificate III",
    "cert 3",
    "now accepting clients",
    "taking new clients",
    "spots available",
    "our team",
    "book now",
    "call us today",
    "registered provider",
    "we offer",
    # Workers advertising themselves
    "available for work",
    "seeking work",
    "I am a support worker",
    "support worker available",
    "experienced carer available",
    "looking for clients",
    "dm me for rates",
]

# Bluesky searches. {service} runs once per entry in leads.api_sources.SERVICE_PHRASES.
BLUESKY_QUERIES = [
    '"looking for" "{service}"',
    '"need" "{service}" ndis',
    '"recommend" "{service}"',
    'ndis "looking for a provider"',
    'ndis "can anyone recommend"',
]

# Selenium web search. DuckDuckGo's plain HTML results page allows crawlers in its
# robots.txt; Reddit, Facebook and Gumtree do not, so their posts are found through it.
# {service} runs once per entry in leads.api_sources.SERVICE_PHRASES.
WEB_SEARCH_SOURCE_NAME = "DuckDuckGo web search (Selenium)"
WEB_SEARCH_SOURCE = {
    "base_url": "https://html.duckduckgo.com/",
    # kl=au-en: Australian results. df=y: published in the last year.
    "search_url_template": "https://html.duckduckgo.com/html/?q={query}&kl=au-en&df=y",
    "selectors": {
        "item": ".web-result:not(.result--no-result)",
        "title": ".result__a",
        "link": "a.result__a",
        "snippet": ".result__snippet",
        "empty": ".result--no-result",
        "allow_undated": True,
        "queries": [
            'ndis melbourne "looking for" "{service}"',
            'site:reddit.com ndis melbourne "looking for"',
            'site:facebook.com/groups ndis melbourne "looking for"',
            'ndis melbourne "can anyone recommend"',
            'ndis melbourne "recommendations" provider',
            'ndis victoria "looking for a provider"',
        ],
    },
}
