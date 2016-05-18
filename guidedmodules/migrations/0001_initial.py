# -*- coding: utf-8 -*-
# Generated by Django 1.9.6 on 2016-05-18 22:06
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion
import jsonfield.fields


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Task',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('module_id', models.CharField(help_text='The ID of the module being completed.', max_length=128)),
                ('title', models.CharField(help_text='The title of this Task. If the user is performing multiple tasks for the same module, this title would distiguish the tasks.', max_length=256)),
                ('notes', models.TextField(blank=True, help_text='Notes set by the user about why they are completing this task.')),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated', models.DateTimeField(auto_now=True, db_index=True)),
                ('extra', jsonfield.fields.JSONField(blank=True, help_text='Additional information stored with this object.')),
            ],
        ),
        migrations.CreateModel(
            name='TaskAnswer',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', jsonfield.fields.JSONField(blank=True, help_text='The actual answer value for the Question, or None/null if the question is not really answered yet.')),
                ('notes', models.TextField(blank=True, help_text='Notes entered by the user completing this TaskAnswer.')),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated', models.DateTimeField(auto_now=True, db_index=True)),
                ('extra', jsonfield.fields.JSONField(blank=True, help_text='Additional information stored with this object.')),
            ],
        ),
        migrations.CreateModel(
            name='TaskQuestion',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('question_id', models.CharField(help_text="The ID of the question (within the Task's module) that this TaskQuestion represents.", max_length=128)),
                ('notes', models.TextField(blank=True, help_text='Notes entered by editors working on this question.')),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated', models.DateTimeField(auto_now=True, db_index=True)),
                ('extra', jsonfield.fields.JSONField(blank=True, help_text='Additional information stored with this object.')),
                ('task', models.ForeignKey(help_text='The Task that this TaskQuestion is a part of.', on_delete=django.db.models.deletion.CASCADE, related_name='questions', to='guidedmodules.Task')),
            ],
        ),
    ]
