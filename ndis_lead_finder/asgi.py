import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ndis_lead_finder.settings")
application = get_asgi_application()
