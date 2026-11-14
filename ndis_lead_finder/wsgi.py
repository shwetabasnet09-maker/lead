import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ndis_lead_finder.settings")
application = get_wsgi_application()
