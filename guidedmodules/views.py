from django.shortcuts import render, redirect, get_object_or_404
from django.http import Http404, HttpResponse, HttpResponseRedirect, HttpResponseForbidden, JsonResponse, HttpResponseNotAllowed
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.utils import timezone
from django.db import transaction

from .models import Module, ModuleQuestion, Task, TaskAnswer, TaskAnswerHistory, InstrumentationEvent
import guidedmodules.module_logic as module_logic
from discussion.models import Discussion
from siteapp.models import User, Invitation, Project, ProjectMembership

@login_required
def new_task(request):
    # Create a new task by answering a module question of a project rook task.
    project = get_object_or_404(Project, id=request.POST["project"], organization=request.organization)

    # Can the user create a task within this project?
    if not project.can_start_task(request.user):
        return HttpResponseForbidden()

    # Create the new subtask.
    task = project.root_task.get_or_create_subtask(request.user, request.POST["question"])

    # Redirect.
    return HttpResponseRedirect(task.get_absolute_url())

@login_required
def next_question(request, taskid, taskslug):
    # Get the Task.
    task = get_object_or_404(Task, id=taskid, project__organization=request.organization)

    # Load the answers the user has saved so far, and fetch imputed
    # answers and next-question info.
    answered = task.get_answers().with_extended_info()

    # Process form data.
    if request.method == "POST":
        # does user have write privs?
        if not task.has_write_priv(request.user):
            return HttpResponseForbidden()

        # normal redirect - reload the page
        redirect_to = request.path

        # validate question
        q = task.module.questions.get(id=request.POST.get("question"))

        # validate and parse value
        if request.POST.get("method") == "clear":
            # clear means that the question returns to an unanswered state
            value = None
            cleared = True
        
        elif request.POST.get("method") == "skip":
            # skipped means the question is answered with a null value
            value = None
            cleared = False
        
        elif request.POST.get("method") == "save":
            # load the answer from the HTTP request
            if q.spec["type"] == "file":
                # File uploads come through request.FILES.
                value = request.FILES.get("value")

                # We allow the user to preserve the existing uploaded value
                # for a question by submitting nothing. (The proper way to
                # clear it is to use Skip.) If the user submits nothing,
                # just return immediately.
                if value is None:
                    return JsonResponse({ "status": "ok", "redirect": redirect_to })

            else:
                # All other values come in as string fields. Because
                # multiple-choice comes as a multi-value field, we get
                # the value as a list. question_input_parser will handle
                # turning it into a single string for other question types.
                value = request.POST.getlist("value")

            # parse & validate
            try:
                value = module_logic.question_input_parser.parse(q, value)
                value = module_logic.validator.validate(q, value)
            except ValueError as e:
                # client side validation should have picked this up
                return JsonResponse({ "status": "error", "message": str(e) })

            # run external functions
            if q.spec['type'] == "external-function":
                try:
                    value = module_logic.run_external_function(q.spec, answered.as_dict())
                except ValueError as e:
                    return JsonResponse({ "status": "error", "message": str(e) })

            cleared = False

        else:
            raise ValueError("invalid 'method' parameter %s" + request.POST.get("method", "<not set>"))

        # save answer - get the TaskAnswer instance first
        question, _ = TaskAnswer.objects.get_or_create(
            task=task,
            question=q,
        )

        # fetch the task that answers this question
        answered_by_tasks = []
        if q.spec["type"] in ("module", "module-set") and not cleared:
            if value == "__new":
                # Create a new task, and we'll redirect to it immediately.
                t = Task.create(
                    parent_task_answer=question, # for instrumentation only, doesn't go into Task instance
                    editor=request.user,
                    project=task.project,
                    module=q.answer_type_module,
                    title=q.answer_type_module.title)

                answered_by_tasks = [t]
                redirect_to = t.get_absolute_url()

            elif value == None:
                # User is skipping this question.
                answered_by_tasks = []

            else:
                # User selects existing Tasks.
                # Validate that the tasks are of the right type (module) and
                # the user has read access.
                answered_by_tasks = [
                    Task.objects.get(id=int(item))
                    for item in value.split(',')
                    ]
                for t in answered_by_tasks:
                    if t.module != q.answer_type_module or not t.has_read_priv(request.user):
                        raise ValueError("invalid task ID")
                if q.spec["type"] == "module" and len(answered_by_tasks) != 1:
                    raise ValueError("did not provide exactly one task ID")
            
            value = None
            answered_by_file = None

        elif q.spec["type"] == "file" and not cleared:
            # Don't save the File into the stored_value field in the database.
            # Instead put it in answered_by_file. value may be None if the user
            # is skipping the question.
            answered_by_file = value
            value = None

        else:
            answered_by_file = None

        # Create a new TaskAnswerHistory record, if the answer is actually changing.
        if cleared:
            # Clear the answer.
            question.clear_answer(request.user)
            instrumentation_event_type = "clear"
        else:
            # Save the answer.
            had_answer = question.has_answer()
            if question.save_answer(value, answered_by_tasks, answered_by_file, request.user):
                # The answer was changed (not just saved as a new answer).
                if request.POST.get("method") == "skip":
                    instrumentation_event_type = "skip"
                elif had_answer:
                    instrumentation_event_type = "change"
                else: # is a new answer
                    instrumentation_event_type = "answer"
            else:
                instrumentation_event_type = "keep"

        # Add instrumentation event.
        # --------------------------
        # How long was it since the question was initially viewed? That gives us
        # how long it took to answer the question.
        i_task_question_view = InstrumentationEvent.objects\
            .filter(user=request.user, event_type="task-question-show", task=task, question=q)\
            .order_by('event_time')\
            .first()
        i_event_value = (timezone.now() - i_task_question_view.event_time).total_seconds() \
            if i_task_question_view else None
        # Save.
        InstrumentationEvent.objects.create(
            user=request.user,
            event_type="task-question-" + instrumentation_event_type,
            event_value=i_event_value,
            module=task.module,
            question=q,
            project=task.project,
            task=task,
            answer=question,
            extra={
                "answer_value": value,
            }
        )

        # return to a GET request
        return JsonResponse({ "status": "ok", "redirect": redirect_to })

    # Ok this is a GET request....

    # What question are we displaying?
    if "q" in request.GET:
        q = task.module.questions.get(key=request.GET["q"])
    else:
        # Display next unanswered question.
        if len(answered.can_answer) == 0:
            q = None # no next question
        else:
            q = answered.can_answer[0]

    if q:
        # Is there a TaskAnswer for this yet?
        taskq = TaskAnswer.objects.filter(task=task, question_id=q.id).first()
    else:
        # We're going to show the finished page - there is no question.
        taskq = None

    # Does the user have read privs here?
    def read_priv():
        if task.has_read_priv(request.user, allow_access_to_deleted=True):
            # See below for checking if the task was deleted.
            return True
        if not taskq:
            return False
        d = Discussion.get_for(request.organization, taskq)
        if not d:
            return False
        if d.is_participant(request.user):
            return True
        return False
    if not read_priv():
        return HttpResponseForbidden()

    # We skiped the check for whether the Task is deleted above. Now
    # check for that.
    if task.deleted_at:
        # The Task is deleted. If the user would have had access to it,
        # show a more friendly page than an access denied. Discussion
        # guests will have been denied above because is_participant
        # will fail on deleted tasks.
        return HttpResponse("This module was deleted by its editor or a project administrator.")

    # Redirect if slug is not canonical. We do this after checking for
    # read privs so that we don't reveal the task's slug to unpriv'd users.
    if request.path != task.get_absolute_url():
        return HttpResponseRedirect(task.get_absolute_url())

    # Display requested question.

    # Common context variables.
    context = {
        "DEBUG": settings.DEBUG,
        "ADMIN_ROOT_URL": settings.SITE_ROOT_URL + "/admin",

        "m": task.module,
        "task": task,
        "is_discussion_guest": not task.has_read_priv(request.user), # i.e. only here for discussion
        "write_priv": task.has_write_priv(request.user),
        "send_invitation": Invitation.form_context_dict(request.user, task.project, [task.editor]),
        "open_invitations": task.get_open_invitations(request.user),
        "source_invitation": task.invitation_history.filter(accepted_user=request.user).order_by('-created').first(),
    }

    if not q:
        # There is no next question - the module is complete.

        # Add instrumentation event.
        # Has the user been here before?
        i_task_done = InstrumentationEvent.objects\
            .filter(user=request.user, event_type="task-done", task=task)\
            .exists()
        # How long since the task was created?
        i_task_create = InstrumentationEvent.objects\
            .filter(user=request.user, event_type="task-create", task=task)\
            .first()
        i_event_value = (timezone.now() - i_task_create.event_time).total_seconds() \
            if i_task_create else None
        # Save.
        InstrumentationEvent.objects.create(
            user=request.user,
            event_type="task-done" if not i_task_done else "task-review",
            event_value=i_event_value,
            module=task.module,
            project=task.project,
            task=task,
        )

        # Construct the page.
        context.update({
            "output": task.render_output_documents(answered),

            # List all questions answered by the user - skipping any answers that were imputed.
            "all_questions": [
                {
                    "question": task.module.questions.get(key=key),
                    "skipped": task.module.questions.get(key=key).spec['type'] != 'interstitial' and (answered.as_dict().get(key) is None),
                }
                for key in answered.as_dict()
                if not key in answered.was_imputed ],
        })
        return render(request, "module-finished.html", context)

    else:
        # A question is going to be shown to the user.

        # Is there an answer already?
        answer = None
        if taskq:
            answer = taskq.get_current_answer()
            if answer and answer.cleared:
                # If the answer is cleared, treat as if it had not been answered.
                answer = None

        # For "module"-type questions, get the Module instance of the tasks that can
        # be an answer to this question, and get the existing Tasks that the user can
        # choose as an answer.
        answer_module = q.answer_type_module
        answer_tasks = []
        if answer_module:
            # The user can choose from any Task instances they have read permission on
            # and that are of the correct Module type.
            answer_tasks = Task.get_all_tasks_readable_by(request.user, request.organization)\
                .filter(module=answer_module)

            # Annotate the instances with whether the user also has write permission.
            for t in answer_tasks:
                t.can_write = t.has_write_priv(request.user)

            # Sort the instances:
            #  first: the current answer, if any
            #  then: tasks defined in the same project as this task
            #  last: everything else
            current_answer = answer.answered_by_task.first() if answer else None
            answer_tasks = sorted(answer_tasks, key = lambda t : (
                not (t == current_answer),
                not (t.project == task.project),
                ))

        # Add instrumentation event.
        # How many times has this question been shown?
        i_prev_view = InstrumentationEvent.objects\
            .filter(user=request.user, event_type="task-question-show", task=task, question=q)\
            .order_by('-event_time')\
            .first()
        # Save.
        InstrumentationEvent.objects.create(
            user=request.user,
            event_type="task-question-show",
            event_value=(i_prev_view.event_value+1) if i_prev_view else 1,
            module=task.module,
            question=q,
            project=task.project,
            task=task,
            answer=taskq,
        )

        # Construct the page.
        prompt = module_logic.render_content({
                "template": q.spec["prompt"],
                "format": "markdown",
            },
            answered,
            "html",
            "%s question %s prompt" % (repr(q.module), q.key)
        )

        context.update({
            "header_col_active": "start" if (len(answered.as_dict()) == 0 and q.spec["type"] == "interstitial") else "questions",
            "q": q,
            "prompt": prompt,
            "history": taskq.get_history() if taskq else None,
            "answer_obj": answer,
            "answer": answer.get_value() if (answer and not answer.cleared) else None,
            "discussion": Discussion.get_for(request.organization, taskq) if taskq else None,
            "show_discussion_members_count": True,
            "show_discussion_h2": True,

            "answer_module": answer_module,
            "answer_tasks": answer_tasks,
            "answer_tasks_show_user": len([ t for t in answer_tasks if t.editor != request.user ]) > 0,

            "context": module_logic.get_question_context(answered, q),
        })
        return render(request, "question.html", context)

@login_required
def instrumentation_record_interaction(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    # Get event variables.
    
    task = get_object_or_404(Task, id=request.POST["task"], project__organization=request.organization)
    if not task.has_read_priv(request.user):
        return HttpResponseForbidden()

    from django.core.exceptions import ObjectDoesNotExist
    try:
        question = task.module.questions.get(id=request.POST.get("question"))
    except ObjectDoesNotExist:
        return HttpResponseForbidden()

    answer = TaskAnswer.objects.filter(task=task, question=question).first()

    # We're recording the *first* interaction, so we'll
    # stop if an interaction has already been recorded.

    if InstrumentationEvent.objects.filter(
        user=request.user, event_type="task-question-interact-first",
        task=task, question=question).exists():
        return HttpResponse("ok")

    # When was the question first viewed? We'll use that
    # to compute the time to first interaction.

    i_task_question_view = InstrumentationEvent.objects\
        .filter(user=request.user, event_type="task-question-show", task=task, question=question)\
        .order_by('event_time')\
        .first()
    event_value = (timezone.now() - i_task_question_view.event_time).total_seconds() \
        if i_task_question_view else None

    # Save.

    InstrumentationEvent.objects.create(
        user=request.user,
        event_type="task-question-interact-first",
        event_value=event_value,
        module=task.module,
        question=question,
        project=task.project,
        task=task,
        answer=answer,
    )

    return HttpResponse("ok")

@login_required
def change_task_state(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    task = get_object_or_404(Task, id=request.POST["id"], project__organization=request.organization)
    if not task.has_write_priv(request.user, allow_access_to_deleted=True):
        return HttpResponseForbidden()

    if request.POST['state'] == "delete":
        task.deleted_at = timezone.now()
    elif request.POST['state'] == "undelete":
        task.deleted_at = None
    else:
        return HttpResponseForbidden()

    task.save(update_fields=["deleted_at"])

    return HttpResponse("ok")

@login_required
def start_a_discussion(request):
    # This view function creates a discussion, or returns an existing one.

    # Validate and retreive the Task and ModuleQuestion that the discussion
    # is to be attached to.
    task = get_object_or_404(Task, id=request.POST['task'])
    q = get_object_or_404(ModuleQuestion, id=request.POST['question'])

    # The user may not have permission to create - only to get.

    tq_filter = { "task": task, "question": q }
    tq = TaskAnswer.objects.filter(**tq_filter).first()
    if not tq:
        # Validate user can create discussion. Any user who can read the task can start
        # a discussion.
        if not task.has_read_priv(request.user):
            return JsonResponse({ "status": "error", "message": "You do not have permission!" })

        # Get the TaskAnswer for this task. It may not exist yet.
        tq, isnew = TaskAnswer.objects.get_or_create(**tq_filter)

    discussion = Discussion.get_for(request.organization, tq)
    if not discussion:
        # Validate user can create discussion.
        if not task.has_read_priv(request.user):
            return JsonResponse({ "status": "error", "message": "You do not have permission!" })

        # Get the Discussion.
        discussion = Discussion.get_for(request.organization, tq, create=True)

    return JsonResponse(discussion.render_context_dict(request.user))


@login_required
def analytics(request):
    from django.db.models import Avg, Count

    from guidedmodules.models import ModuleQuestion

    if not request.user.is_staff:
        return HttpResponseForbidden()

    def compute_table(opt):
        qs = InstrumentationEvent.objects\
            .filter(event_type=opt["event_type"])\
            .values(opt["field"])

        # When we look at the analytics page in an organization domain,
        # we only pull instrumentation for projects within that organization.
        if hasattr(request, "organization"):
            qs = qs.filter(project__organization=request.organization)

        overall = qs.aggregate(
                avg_value=Avg('event_value'),
                count=Count('event_value'),
            )
        
        rows = qs\
            .exclude(**{opt["field"]: None})\
            .annotate(
                avg_value=Avg('event_value'),
                count=Count('event_value'),
            )\
            .exclude(avg_value=None)\
            .order_by('-avg_value')\
            [0:10]
        
        bulk_objs = opt['model'].objects.in_bulk(r[opt['field']] for r in rows)

        opt.update({
            "overall": round(overall['avg_value']),
            "n": overall['count'],
            "rows": [{
                    "obj": str(bulk_objs[v[opt['field']]]),
                    "label": opt['label']( bulk_objs[v[opt['field']]] ),
                    "detail": opt['detail']( bulk_objs[v[opt['field']]] ),
                    "n": v['count'],
                    "value": round(v['avg_value']),
                }
                for v in rows ],
        })
        return opt

    return render(request, "analytics.html", {
        "base_template": "base.html" if hasattr(request, "organization") else "base-landing.html",
        "tables": [
            compute_table({
                "model": Module,
                "event_type": "task-done",
                "field": "module",
                "title": "Hardest Modules",
                "quantity": "Time To Finish (sec)",
                "label": lambda m : m.title,
                "detail": lambda m : "version id %d" % m.id,
            }),

            compute_table({
                "model": ModuleQuestion,
                "event_type": "task-question-answer",
                "title": "Hardest Questions",
                "field": "question",
                "quantity": "Time To Answer (sec)",
                "label": lambda q : q.spec['title'],
                "detail": lambda q : "%s, version id %d" % (q.module.title, q.module.id),
            }),

            compute_table({
                "model": ModuleQuestion,
                "event_type": "task-question-interact-first",
                "title": "Longest Time to First Interaction",
                "field": "question",
                "quantity": "Time To First Interaction (sec)",
                "label": lambda q : q.spec['title'],
                "detail": lambda q : "%s, version id %d" % (q.module.title, q.module.id),
            }),
        ]
    })
