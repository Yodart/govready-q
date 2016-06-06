from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseForbidden, JsonResponse, HttpResponseNotAllowed
from django.core.urlresolvers import reverse
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .models import User, Project, Invitation
from guidedmodules.models import Module, Task, ProjectMembership
from discussion.models import Discussion

from .good_settings_helpers import AllauthAccountAdapter # ensure monkey-patch is loaded

def homepage(request):
    if not request.user.is_authenticated():
        # Public homepage.
        return render(request, "index.html")

    settings_task = request.user.get_settings_task()
    if not settings_task.is_finished():
        # First task: Fill out your account settings.
        return HttpResponseRedirect(settings_task.get_absolute_url()
            + "/start")

    else:
        # Ok, show user what they can do --- list the projects they
        # are involved with.
        projects = set()

        # Add all of the Projects the user is a member of.
        for pm in ProjectMembership.objects.filter(user=request.user):
            projects.add(pm.project)
            if pm.is_admin:
                # Annotate with whether the user is an admin of the project.
                pm.project.user_is_admin = True

        # Add projects that the user is the editor of a task in, even if
        # the user isn't a team member of that project.
        for task in Task.get_all_tasks_readable_by(request.user).order_by('-created'):
            projects.add(task.project)

        # Add projects that the user is participating in a Discussion in
        # as a guest.
        for d in Discussion.objects.filter(guests=request.user):
            projects.add(d.attached_to.task.project)

        # Sort.
        projects = sorted(projects, key = lambda x : x.updated, reverse=True)

        return render(request, "home.html", {
            "projects": projects,
            "any_have_members_besides_me": ProjectMembership.objects.filter(project__in=projects).exclude(user=request.user),
        })

@login_required
def new_project(request):
    from django.forms import ModelForm

    class NewProjectForm(ModelForm):
        class Meta:
            model = Project
            fields = ['title', 'notes']
            help_texts = {
                'title': 'Give your project a descriptive name.',
                'notes': 'Optionally write some notes. If you invite other users to your project team, they\'ll be able to see this too.',
            }

    form = NewProjectForm()
    if request.method == "POST":
        # Save and then go back to the home page to see it.
        form = NewProjectForm(request.POST)
        if not form.errors:
            with transaction.atomic():
                # create object
                project = form.save()

                # set root task
                m = Module.objects.get(key="project", superseded_by=None)
                task = Task.objects.create(
                    project=project,
                    editor=request.user,
                    module=m,
                    title=m.title)
                project.root_task = task
                project.save()

                # add user as an admin
                ProjectMembership.objects.create(
                    project=project,
                    user=request.user,
                    is_admin=True)
            return HttpResponseRedirect("/")

    return render(request, "new-project.html", {
        "first": not ProjectMembership.objects.filter(user=request.user).exists(),
        "form": form,
    })


@login_required
def project(request, project_id):
    project = get_object_or_404(Project, id=project_id)

    # Check authorization.
    if not project.has_read_priv(request.user):
        return HttpResponseForbidden()

    # Redirect if slug is not canonical. We do this after checking for
    # read privs so that we don't reveal the task's slug to unpriv'd users.
    if request.path != project.get_absolute_url():
        return HttpResponseRedirect(task.get_absolute_url())

    # Get the project team members.
    project_members = ProjectMembership.objects.filter(project=project)
    is_project_member = project_members.filter(user=request.user).exists()

    # Get all of the discussions I'm participating in as a guest in this project.
    # Meaning, I'm not a member, but I still need access to certain tasks and
    # certain questions within those tasks.
    discussions = list(project.get_discussions_in_project_as_guest(request.user))

    # Create all of the module entries in a tabs & groups data structure.
    from collections import OrderedDict
    tabs = OrderedDict()
    for mq in project.root_task.module.questions.all().order_by('definition_order'):
        if mq.spec.get("type") not in ("module", "module-set"):
            continue

        # Create the tab and group for this.
        tabname = mq.spec.get("tab", "Modules")
        tab = tabs.setdefault(tabname, {
            "title": tabname,
            "groups": OrderedDict(),
        })
        groupname = mq.spec.get("group", "Modules")
        group = tab["groups"].setdefault(groupname, {
            "title": groupname,
            "modules": [],
        })

        # Is this question answered yet? Are there any discussions the user
        # is a guest of in any of the tasks that answer this question?
        tasks = []
        task_discussions = []
        ans = project.root_task.answers.filter(question=mq).first()
        if ans:
            ans = ans.get_current_answer()
            for task in ans.answered_by_task.all():
                tasks.append(task)
                task.has_write_priv = task.has_write_priv(request.user)
                task_discussions.extend([d for d in discussions if d.attached_to.task == task])

        # Do not display if user should not be able to see this task.
        if not is_project_member and len(task_discussions) == 0:
            continue

        # Add entry.
        group["modules"].append({
            "question": mq,
            "module": Module.objects.get(id=mq.spec["module-id"]),
            "tasks": tasks,
            "can_start_new_task": mq.spec["type"] == "module-set" or len(tasks) == 0,
            "discussions": task_discussions,
        })

    # Additional tabs of content.
    additional_tabs = []
    if project.root_task.module.spec.get("output"):
    	for doc in project.root_task.render_output_documents(hard_fail=False):
    		if doc.get("tab") in tabs:
    			# Assign this to one of the tabs.
    			tabs[doc["tab"]]["intro"] = doc
    		else:
    			# Add tab to end.
    			additional_tabs.append(doc)

    # Render.
    return render(request, "project.html", {
        "is_admin": request.user in project.get_admins(),
        "is_member": is_project_member,
        "can_begin_module": (request.user in project.get_admins()) and not project.is_account_project,
        "project_has_members_besides_me": project and project.members.exclude(user=request.user),
        "project": project,
        "title": project.title,
        "intro" : project.root_task.render_introduction() if project.root_task.module.spec.get("introduction") else "",
        "additional_tabs": additional_tabs,
        "open_invitations": [
            inv for inv in Invitation.objects.filter(from_user=request.user, from_project=project, accepted_at=None, revoked_at=None).order_by('-created')
            if not inv.is_expired() ],
        "send_invitation": Invitation.form_context_dict(request.user, project),
        "project_members": sorted(project_members, key = lambda mbr : (not mbr.is_admin, str(mbr.user))),
        "tabs": list(tabs.values()),
    })
    

# INVITATIONS

@login_required
def send_invitation(request):
    import email_validator
    if request.method != "POST": raise HttpResponseNotAllowed(['POST'])
    try:
        if not request.POST['user_id'] and not request.POST['user_email']:
            raise ValueError("Select a team member or enter an email address.")

        if request.POST['user_email']:
            email_validator.validate_email(request.POST['user_email'])

        # Validate that the user is a member of from_project. Is None
        # if user is not a project member.
        from_project = Project.objects.filter(id=request.POST["project"], members__user=request.user).first()

        # Authorization for adding invitee to the project team.
        if not from_project:
            into_project = False
        else:
            inv_ctx = Invitation.form_context_dict(request.user, from_project)
            into_project = (request.POST.get("add_to_team", "") != "") and inv_ctx["can_add_invitee_to_team"]

        # Target.
        if request.POST.get("into_new_task_question_id"):
            # validate the question ID
            target = from_project
            target_info = {
                "into_new_task_question_id": from_project.root_task.module.questions.filter(id=request.POST.get("into_new_task_question_id")).get().id,
            }

        elif request.POST.get("into_task_editorship"):
            target = Task.objects.get(id=request.POST["into_task_editorship"])
            if target.editor != request.user:
                raise HttpResponseForbidden()
            if from_project and target.project != from_project:
                return HttpResponseForbidden()

            # from_project may be None if the requesting user isn't a project
            # member, but they may transfer editorship and so in that case we'll
            # set from_project to the Task's project
            from_project = target.project
            target_info =  {
                "what": "editor",
            }

        elif "into_discussion" in request.POST:
            target = get_object_or_404(Discussion, id=request.POST["into_discussion"])
            if not target.can_invite_guests(request.user):
                return HttpResponseForbidden()
            target_info = {
                "what": "invite-guest",
            }

        else:
            target = from_project
            target_info = {
                "what": "join-team",
            }

        inv = Invitation.objects.create(
            # who is sending the invitation?
            from_user=request.user,
            from_project=from_project,

            # what is the recipient being invited to? validate that the user is an admin of this project
            # or an editor of the task being reassigned.
            into_project=into_project,
            target=target,
            target_info=target_info,

            # who is the recipient of the invitation?
            to_user=User.objects.get(id=request.POST["user_id"]) if request.POST.get("user_id") else None,
            to_email=request.POST.get("user_email"),

            # personalization
            text=request.POST.get("message", ""),
            email_invitation_code=Invitation.generate_email_invitation_code(),
        )

        inv.send() # TODO: Move this into an asynchronous queue.

        return JsonResponse({ "status": "ok" })

    except ValueError as e:
        return JsonResponse({ "status": "error", "message": str(e) })
    except Exception as e:
        import sys
        sys.stderr.write(str(e) + "\n")
        return JsonResponse({ "status": "error", "message": "There was a problem -- sorry!" })

@login_required
def cancel_invitation(request):
    inv = get_object_or_404(Invitation, id=request.POST['id'], from_user=request.user)
    inv.revoked_at = timezone.now()
    inv.save(update_fields=['revoked_at'])
    return JsonResponse({ "status": "ok" })

def accept_invitation(request, code=None):
    assert code.strip() != ""
    inv = get_object_or_404(Invitation, email_invitation_code=code)

    from django.contrib.auth import authenticate, login, logout
    from django.contrib import messages
    from django.http import HttpResponseRedirect
    import urllib.parse

    # If this is a repeat-click, just redirect the user to where
    # they went the first time.
    if inv.accepted_at:
        return HttpResponseRedirect(inv.get_redirect_url())

    # Can't accept if this object has expired. Warn the user but
    # send them to the homepage.
    if inv.is_expired():
        messages.add_message(request, messages.ERROR, 'The invitation you wanted to accept has expired.')
        return HttpResponseRedirect("/")

    # Get the user logged into an account.
    
    matched_user = inv.to_user \
        or User.objects.filter(email=inv.to_email).exclude(id=inv.from_user.id).first()
    
    if request.user.is_authenticated() and request.GET.get("accept-auth") == "1":
        # The user is logged in and the "auth" flag is set, so let the user
        # continue under this account. This code path occurs when the user
        # first reaches this view but is not authenticated as the user that
        # was invited. We then send them to create an account or log in.
        # The "next" URL on that login screen adds "auth=1", so that when
        # we come back here, we just accept whatever account they created
        # or logged in to. The meaning of "auth" is the User's desire to
        # continue with their existing credentials. We don't go through
        # this path on the first run because the user may not want to
        # accept the invitation under an account they happened to be logged
        # in as.
        pass

    elif matched_user and request.user == matched_user:
        # If the invitation was to a user account, and the user is already logged
        # in to it, then we're all set. Or if the invitation was sent to an email
        # address already associated with a User account and the user is logged
        # into that account, then we're all set.
        pass

    elif matched_user:
        # If the invitation was to a user account or to an email address that has
        # an account, but the user wasn't already logged in under that account,
        # then since the user on this request has just demonstrated ownership of
        # that user's email address, we can log them in immediately.
        matched_user = authenticate(user_object=matched_user)
        if not matched_user.is_active:
            messages.add_message(request, messages.ERROR, 'Your account has been deactivated.')
            return HttpResponseRedirect("/")
        if request.user.is_authenticated():
            # The user was logged into a different account before. Log them out
            # of that account and then log them into the account in the invitation.
            logout(request) # setting a message after logout but before login should keep the message in the session
            messages.add_message(request, messages.INFO, 'You have been logged in as %s.' % matched_user)
        login(request, matched_user)

    else:
        # The invitation was sent to an email address that does not have a matching
        # User account (if it did, we would have logged the user in immediately because
        # they just confirmed ownership of the address). Ask the user to log in or sign up,
        # redirecting back to this page after with "auth=1" so that we skip the matched
        # user check and accept whatever account the user just logged into or created.
        #
        # In the event the user was already logged into an account that didn't match the
        # invitation email address, log them out now.
        from urllib.parse import urlencode
        logout(request)
        return HttpResponseRedirect(reverse("account_login") + "?" + urlencode({
            "next": request.path + "?accept-auth=1",
        }))

    # The user is now logged in and able to accept the invitation.
    with transaction.atomic():

        inv.accepted_at = timezone.now()
        inv.accepted_user = request.user

        def add_message(message):
            messages.add_message(request, messages.INFO, message)

        # Add user to a project team.
        if inv.into_project:
            ProjectMembership.objects.create(
                project=inv.from_project,
                user=request.user,
                )
            add_message('You have joined the team %s.' % inv.from_project.title)

        # Run the target's invitation accept function.
        inv.target.accept_invitation(inv, add_message)

        # Update this invitation.
        inv.save()

        # TODO: Notify inv.from_user that the invitation was accepted.
        #       Create other notifications?

        return HttpResponseRedirect(inv.get_redirect_url())
