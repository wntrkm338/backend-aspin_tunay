from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('admin_api', '0026_alter_adoptionapplication_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='adoptionapplication',
            name='status',
            field=models.CharField(
                choices=[
                    ('Pending', 'Pending'),
                    ('Under Review', 'Under Review'),
                    ('Approved', 'Approved'),
                    ('Declined', 'Declined'),
                    ('Assessment 1 Scheduled', 'Assessment 1 Scheduled'),
                    ('Assessment 1 Rescheduled', 'Assessment 1 Rescheduled'),
                    ('Assessment 1 Passed', 'Assessment 1 Passed'),
                    ('Assessment 1 Rejected', 'Assessment 1 Rejected'),
                    ('Verification Rejected', 'Verification Rejected'),
                    ('Verified Adopted', 'Verified Adopted'),
                    ('Post-Adoption Scheduled', 'Post-Adoption Scheduled'),
                    ('Post-Adoption Rejected', 'Post-Adoption Rejected'),
                    ('Monitoring Passed', 'Monitoring Passed'),
                    ('Awaiting Feed Post', 'Awaiting Feed Post'),
                    ('Completed', 'Completed'),
                ],
                default='Pending',
                max_length=50,
            ),
        ),
    ]
