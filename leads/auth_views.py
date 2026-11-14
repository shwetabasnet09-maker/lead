from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.views import LoginView
from django.utils import timezone
from django.utils.crypto import salted_hmac

from .models import LoginThrottle


class ThrottledLoginView(LoginView):
    max_failures = 5
    window = timedelta(minutes=15)
    lock_duration = timedelta(minutes=15)

    def _identifier(self):
        username = self.request.POST.get("username", "").strip().lower()
        remote_ip = self.request.META.get("REMOTE_ADDR", "unknown")
        return salted_hmac("ndis-lead-finder-login", f"{username}|{remote_ip}").hexdigest()

    def post(self, request, *args, **kwargs):
        identifier = self._identifier()
        now = timezone.now()
        throttle = LoginThrottle.objects.filter(identifier_hash=identifier).first()
        if throttle and throttle.locked_until and throttle.locked_until > now:
            form = self.get_form()
            form.add_error(None, "Too many unsuccessful sign-in attempts. Try again in 15 minutes.")
            return self.form_invalid(form)

        response = super().post(request, *args, **kwargs)
        if response.status_code in {301, 302, 303}:
            LoginThrottle.objects.filter(identifier_hash=identifier).delete()
            return response

        throttle, _ = LoginThrottle.objects.get_or_create(identifier_hash=identifier)
        if now - throttle.first_failed_at > self.window:
            throttle.failures = 0
            throttle.first_failed_at = now
        throttle.failures += 1
        throttle.last_failed_at = now
        if throttle.failures >= self.max_failures:
            throttle.locked_until = now + self.lock_duration
            messages.error(request, "Too many unsuccessful sign-in attempts. Sign-in is temporarily locked.")
        throttle.save()
        return response
