from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("leads", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="LoginThrottle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("identifier_hash", models.CharField(max_length=64, unique=True)),
                ("failures", models.PositiveSmallIntegerField(default=0)),
                ("first_failed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_failed_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("locked_until", models.DateTimeField(blank=True, null=True)),
            ],
        )
    ]
