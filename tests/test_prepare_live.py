from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from leads.management.commands.seed_demo_leads import DEMO_SOURCE_NAME
from leads.models import Lead, Membership, Notification, Organisation, Source


def run(*args, **kwargs):
    call_command("prepare_live", *args, stdout=StringIO(), **kwargs)


@override_settings(GEMINI_API_KEY="", GROQ_API_KEY="")
class PrepareLiveTests(TestCase):
    def load_demo(self):
        with override_settings(DEBUG=True):
            call_command("seed_demo_leads", stdout=StringIO())

    def test_removes_demo_data_but_keeps_real_leads(self):
        User.objects.create_superuser("owner", password="x")
        self.load_demo()
        self.assertTrue(Lead.objects.filter(source__name=DEMO_SOURCE_NAME).exists())
        self.assertTrue(Notification.objects.exists())
        real_source = Source.objects.create(name="Real", source_type="rss", base_url="https://r.example", search_url_template="https://r.example")
        real = Lead.objects.create(organisation=Organisation.objects.get(slug="default"), source=real_source, source_url="https://r.example/1", title="Real", content_hash="r")
        real_notification = Notification.objects.create(user=User.objects.get(username="owner"), title="New likely service request", message="A real post")

        run()

        self.assertFalse(Source.objects.filter(name=DEMO_SOURCE_NAME).exists())
        self.assertEqual(list(Lead.objects.all()), [real])
        self.assertEqual(list(Notification.objects.all()), [real_notification])

    def test_orphaned_demo_notifications_are_removed(self):
        user = User.objects.create_superuser("owner", password="x")
        Notification.objects.create(user=user, title="New likely service request", message="Looking for a plan manager – Dandenong")
        run()
        self.assertFalse(Notification.objects.exists())

    def test_demo_data_cannot_be_loaded_on_a_live_site(self):
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            call_command("seed_demo_leads", stdout=StringIO())
        self.assertFalse(Source.objects.filter(name=DEMO_SOURCE_NAME).exists())

    def test_fresh_database_gets_defaults_and_nothing_runs_unless_approved(self):
        owner = User.objects.create_superuser("owner", password="x")
        output = StringIO()
        call_command("prepare_live", stdout=output)
        self.assertIn("automatic scraping will find nothing", output.getvalue())
        self.assertFalse([source for source in Source.objects.all() if source.is_runnable])
        self.assertTrue(Membership.objects.filter(user=owner, role=Membership.Role.OWNER).exists())

    def test_only_the_named_source_is_approved(self):
        run(approve_source=["Mastodon public hashtags"])
        self.assertEqual([source.name for source in Source.objects.all() if source.is_runnable], ["Mastodon public hashtags"])

    def test_running_twice_changes_nothing(self):
        run(approve_source=["Mastodon public hashtags"])
        count = Source.objects.count()
        run(approve_source=["Mastodon public hashtags"])
        self.assertEqual(Source.objects.count(), count)

    def test_unknown_source_is_an_error(self):
        with self.assertRaises(CommandError):
            run(approve_source=["Nope"])
