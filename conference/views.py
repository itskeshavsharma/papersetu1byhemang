from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import ConferenceForm, ReviewerVolunteerForm, PaperSubmissionForm
from .models import Conference, ReviewerPool, ReviewInvite, UserConferenceRole, Paper, Review
from accounts.models import User
from django.utils.crypto import get_random_string
from django.http import Http404
from django.core.mail import send_mail
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse

@login_required
def create_conference(request):
    if request.method == 'POST':
        form = ConferenceForm(request.POST)
        if form.is_valid():
            conference = form.save(commit=False)
            conference.chair = request.user
            conference.status = 'upcoming'
            conference.invite_link = get_random_string(32)
            conference.is_approved = False
            conference.requested_by = request.user
            conference.save()
            UserConferenceRole.objects.create(user=request.user, conference=conference, role='chair')
            # Send pending approval email to superuser(s)
            User = get_user_model()
            superusers = User.objects.filter(is_superuser=True)
            superuser_emails = [u.email for u in superusers if u.email]
            if superuser_emails:
                send_mail(
                    'Conference Request Pending',
                    f'A new conference request ("{conference.name}") is pending approval.',
                    'admin@example.com',
                    superuser_emails,
                )
            messages.success(request, 'Conference request submitted and pending admin approval!')
            return redirect('dashboard:dashboard')
        else:
            # Form is invalid, show errors and stay on the form
            return render(request, 'conference/create_conference.html', {'form': form})
    else:
        form = ConferenceForm()
    return render(request, 'conference/create_conference.html', {'form': form})

@login_required
def reviewer_volunteer(request):
    if hasattr(request.user, 'reviewer_profile'):
        messages.info(request, 'You have already volunteered as a reviewer.')
        return redirect('dashboard:dashboard')
    if request.method == 'POST':
        form = ReviewerVolunteerForm(request.POST)
        if form.is_valid():
            reviewer = form.save(commit=False)
            reviewer.user = request.user
            # Save first and last name to user
            request.user.first_name = form.cleaned_data['first_name']
            request.user.last_name = form.cleaned_data['last_name']
            request.user.save()
            reviewer.save()
            messages.success(request, 'Thank you for volunteering as a reviewer!')
            return redirect('dashboard:dashboard')
    else:
        form = ReviewerVolunteerForm()
    return render(request, 'conference/reviewer_volunteer.html', {'form': form})

@login_required
def submit_paper(request, conference_id):
    conference = get_object_or_404(Conference, id=conference_id)
    if request.method == 'POST':
        form = PaperSubmissionForm(request.POST, request.FILES)
        if form.is_valid():
            paper = form.save(commit=False)
            paper.author = request.user
            paper.conference = conference
            paper.save()
            UserConferenceRole.objects.get_or_create(user=request.user, conference=conference, role='author')
            messages.success(request, 'Paper submitted successfully!')
            return redirect('dashboard:dashboard')
    else:
        form = PaperSubmissionForm()
    return render(request, 'conference/submit_paper.html', {'form': form, 'conference': conference})

@login_required
def join_conference(request, invite_link):
    try:
        conference = Conference.objects.get(invite_link=invite_link)
    except Conference.DoesNotExist:
        raise Http404('Conference not found.')
    if request.method == 'POST':
        # Add user as author if not already
        UserConferenceRole.objects.get_or_create(user=request.user, conference=conference, role='author')
        messages.success(request, f'You have joined the conference "{conference.name}"!')
        return redirect('dashboard:dashboard')
    return render(request, 'conference/join_conference.html', {'conference': conference})

@login_required
def conferences_list(request):
    """List all available conferences that the user can view"""
    search_query = request.GET.get('search', '')
    area_filter = request.GET.get('area', '')
    # Only show conferences where user is chair, pc_member, or author
    conferences = Conference.objects.filter(
        Q(chair=request.user) |
        Q(userconferencerole__user=request.user, userconferencerole__role__in=['author', 'pc_member'])
    ).distinct().filter(is_approved=True, status__in=['upcoming', 'live'])
    # Apply search filter
    if search_query:
        conferences = conferences.filter(
            Q(name__icontains=search_query) | 
            Q(acronym__icontains=search_query) |
            Q(description__icontains=search_query)
        )
    # Apply area filter
    if area_filter:
        conferences = conferences.filter(primary_area=area_filter)
    # Add user role information
    for conference in conferences:
        conference.user_roles = UserConferenceRole.objects.filter(
            user=request.user, 
            conference=conference
        ).values_list('role', flat=True)
        if conference.chair == request.user and 'chair' not in conference.user_roles:
            conference.user_roles = list(conference.user_roles) + ['chair']
    from .models import AREA_CHOICES
    context = {
        'conferences': conferences,
        'search_query': search_query,
        'area_filter': area_filter,
        'area_choices': AREA_CHOICES,
    }
    return render(request, 'conference/conferences_list.html', context)

@login_required
def choose_conference_role(request, conference_id):
    conference = get_object_or_404(Conference, id=conference_id)
    user = request.user
    roles = []
    if conference.chair == user:
        roles.append('chair')
    user_roles = UserConferenceRole.objects.filter(user=user, conference=conference).values_list('role', flat=True)
    for r in user_roles:
        if r not in roles:
            roles.append(r)
    # Always show pc_member if user has that role
    if 'pc_member' in user_roles and 'pc_member' not in roles:
        roles.append('pc_member')
    # Always show author role for everyone
    if 'author' not in roles:
        roles.append('author')
    role_links = []
    for role in roles:
        if role == 'chair':
            url = reverse('dashboard:chair_conference_detail', args=[conference.id])
            label = 'Chair'
        elif role == 'author':
            url = reverse('conference:author_dashboard', args=[conference.id])
            label = 'Author (Upload Paper)'
        elif role == 'reviewer':
            url = reverse('dashboard:dashboard') + f'?view=reviewer&conf_id={conference.id}'
            label = 'Reviewer'
        elif role == 'pc_member':
            url = reverse('dashboard:pc_conference_detail', args=[conference.id])
            label = 'PC Member'
        else:
            continue
        role_links.append({'role': role, 'url': url, 'label': label})
    context = {
        'conference': conference,
        'role_links': role_links,
    }
    return render(request, 'conference/choose_role.html', context)

@login_required
def author_dashboard(request, conference_id):
    conference = get_object_or_404(Conference, id=conference_id)
    user = request.user
    # Get all papers by this user for this conference
    papers = Paper.objects.filter(conference=conference, author=user)
    message = ''
    if request.method == 'POST':
        title = request.POST.get('title')
        abstract = request.POST.get('abstract')
        file = request.FILES.get('file')
        authors_data = request.POST.getlist('authors[]')
        if title and file:
            paper = Paper.objects.create(title=title, abstract=abstract, file=file, author=user, conference=conference)
            # Add additional authors (excluding the submitting user)
            for author_str in authors_data:
                name_email = [a.strip() for a in author_str.split(',')]
                if len(name_email) == 2:
                    name, email = name_email
                    # You can extend this to create a PaperAuthor model if needed
            UserConferenceRole.objects.get_or_create(user=user, conference=conference, role='author')
            message = 'Paper submitted successfully!'
            papers = Paper.objects.filter(conference=conference, author=user)
    context = {
        'conference': conference,
        'papers': papers,
        'message': message,
    }
    return render(request, 'conference/author_dashboard.html', context)

nav_items = [
    "Submissions", "Reviews", "Status", "PC", "Events",
    "Email", "Administration", "Conference", "News", "papersetu"
]
context = {
    # ... your existing context ...
    'nav_items': nav_items,
    # Optionally, set 'active_tab': 'Submissions' or whichever is active
} 