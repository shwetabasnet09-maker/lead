from django.conf import settings

from .models import Notification


def app_context(request):
    unread = 0
    can_manage = False
    if request.user.is_authenticated:
        from .settings_views import can_manage as user_can_manage

        unread = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        can_manage = user_can_manage(request.user)
    return {"APP_NAME": settings.APP_NAME, "unread_notification_count": unread, "can_manage": can_manage}
