"""Default search keywords for a profile.

These are written the way people who need care actually post ("need someone to
help mum shower"), not as service names, which mostly match provider ads and job
posts. Category ``None`` means the keyword applies to any service.
"""

POSITIVE_KEYWORDS = {
    None: [
        # Asking for help in general
        "looking for a carer",
        "need a carer",
        "carer needed",
        "looking for a support worker",
        "need a support worker",
        "anyone recommend a carer",
        "can anyone recommend",
        "need help for my mum",
        "need help for my dad",
        "help for my elderly",
        "care for my mother",
        "care for my father",
        "help at home for",
        "someone to help my",
        "looking for someone to help",
        "need someone to look after",
        # How many hours or days they need
        "few hours a day",
        "couple of hours",
        "2 hours a day",
        "hours per week",
        "days a week",
        "weekly help",
        "overnight care",
        "live in carer",
        "after hospital",
        "after surgery",
        "just discharged",
    ],
    "Personal care": ["help with showering", "help showering", "help getting dressed", "help with personal care"],
    "Home care / in-home support": ["in home carer needed", "home help needed", "someone to check on"],
    "Domestic assistance / cleaning": ["need a cleaner for my", "help with housework", "cleaner for elderly"],
    "Meal preparation": ["help with cooking", "meals for my"],
    "Transport": ["need a lift to appointments", "someone to drive"],
    "Nursing": ["need a nurse at home", "wound care at home"],
    "Respite / Short-Term Accommodation": ["need a break from caring", "respite needed"],
    "Community participation": ["someone to take my", "companion for"],
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
    "our team",
    "book now",
    "call us today",
    "registered provider",
    "we offer",
    # Workers advertising themselves
    "available for work",
    "seeking work",
    "I am a support worker",
    "experienced carer available",
    "looking for clients",
]
