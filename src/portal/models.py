"""The schema. Invariants that must hold whatever code path writes a row live
in the database: foreign keys, unique and check constraints here, and the
triggers in the migrations (deadline, team consistency, score range,
conflict of interest, the append-only audit chain)."""
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower, Now


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra):
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra):
        extra.update(is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)


class User(AbstractUser):
    """Signs in with an email address. `is_staff` is the global admin role;
    every other role is per event, in Membership."""
    username = None
    first_name = None
    last_name = None
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200, blank=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(email=Lower("email")), name="user_email_lowercase")]

    def __str__(self):
        return self.name or self.email


class ApiToken(models.Model):
    """Only the SHA-256 of a token is stored. The token itself is shown once."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_tokens")
    label = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class Event(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    external_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    submissions_open = models.DateTimeField()
    submissions_close = models.DateTimeField()
    judging_open = models.DateTimeField(null=True, blank=True)
    judging_close = models.DateTimeField(null=True, blank=True)
    voting_open = models.DateTimeField(null=True, blank=True)
    voting_close = models.DateTimeField(null=True, blank=True)
    results_published_at = models.DateTimeField(null=True, blank=True)
    reviews_per_project = models.PositiveSmallIntegerField(default=3)
    max_team_size = models.PositiveSmallIntegerField(default=4)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(submissions_open__lt=F("submissions_close")), name="event_submission_window"),
            models.CheckConstraint(
                condition=Q(judging_open__isnull=True) | Q(judging_close__isnull=True) | Q(judging_open__lt=F("judging_close")),
                name="event_judging_window"),
            models.CheckConstraint(
                condition=Q(voting_open__isnull=True) | Q(voting_close__isnull=True) | Q(voting_open__lt=F("voting_close")),
                name="event_voting_window"),
            models.CheckConstraint(condition=Q(reviews_per_project__gte=1), name="event_k_positive"),
            models.CheckConstraint(condition=Q(max_team_size__gte=1), name="event_team_size_positive"),
        ]

    def __str__(self):
        return self.name


class Track(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="tracks")
    name = models.CharField(max_length=200)
    external_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "name"], name="track_name_per_event"),
            models.UniqueConstraint(fields=["event", "external_id"], name="track_external_id_per_event"),
        ]

    def __str__(self):
        return self.name


class Prize(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="prizes")
    track = models.ForeignKey(Track, on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["event", "name"], name="prize_name_per_event")]


class Membership(models.Model):
    class Role(models.TextChoices):
        PARTICIPANT = "participant"
        JUDGE = "judge"
        ORGANIZER = "organizer"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    # For judges: the tracks they may be assigned in. Empty means any track.
    tracks = models.ManyToManyField(Track, blank=True)
    external_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "event", "role"], name="membership_unique_role"),
            models.UniqueConstraint(fields=["event", "role", "external_id"], name="membership_external_id"),
        ]


class Team(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="teams")
    name = models.CharField(max_length=200)
    external_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Team names aren't unique: the fixture has three different teams
        # called StillTrail. Teams are told apart by id.
        constraints = [models.UniqueConstraint(fields=["event", "external_id"], name="team_external_id_per_event")]

    def __str__(self):
        return self.name


class TeamMember(models.Model):
    # event is a copy of team.event (a trigger keeps them equal) so that the
    # database can say "one team per person per event" as a unique constraint.
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    event = models.ForeignKey(Event, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="team_memberships")
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["event", "user"], name="one_team_per_user_per_event")]


class TeamInvite(models.Model):
    """A single-use link. Only the hash of its token is stored."""
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="invites")
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(used_by__isnull=True) | Q(used_at__isnull=False), name="invite_used_has_time")]


class Project(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft"
        SUBMITTED = "submitted"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="projects")
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="projects")
    track = models.ForeignKey(Track, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=200)
    tagline = models.CharField(max_length=300, blank=True)
    summary = models.TextField(blank=True)
    description = models.TextField(blank=True)
    repo_url = models.URLField(max_length=500, blank=True)
    demo_url = models.URLField(max_length=500, blank=True)
    tags = ArrayField(models.CharField(max_length=40), default=list, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    # Set when a submission repeats another one; it stays out of the rankings
    # until an organizer decides.
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="duplicates")
    external_id = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["event", "external_id"], name="project_external_id_per_event"),
            models.CheckConstraint(
                condition=Q(status="submitted", submitted_at__isnull=False) | Q(status="draft", submitted_at__isnull=True),
                name="project_submitted_has_time"),
            models.CheckConstraint(condition=~Q(duplicate_of=F("pk")), name="project_not_own_duplicate"),
        ]
        indexes = [models.Index(fields=["event", "status"])]

    def __str__(self):
        return self.title


class RubricCriterion(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="criteria")
    key = models.SlugField(max_length=50)
    name = models.CharField(max_length=100)
    weight = models.DecimalField(max_digits=6, decimal_places=3, default=1)
    min_value = models.SmallIntegerField(default=1)
    max_value = models.SmallIntegerField(default=5)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["event", "key"], name="criterion_key_per_event"),
            models.CheckConstraint(condition=Q(weight__gt=0), name="criterion_weight_positive"),
            models.CheckConstraint(condition=Q(min_value__lt=F("max_value")), name="criterion_range"),
        ]

    def __str__(self):
        return self.name


class JudgeAssignment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        DONE = "done"
        RECUSED = "recused"

    class Source(models.TextChoices):
        SEED = "seed"
        AUTO = "auto"
        MANUAL = "manual"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="assignments")
    judge = models.ForeignKey(User, on_delete=models.CASCADE, related_name="assignments")
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="assignments")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.AUTO)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["judge", "project"], name="assignment_unique")]
        indexes = [models.Index(fields=["event", "judge"])]


class Review(models.Model):
    """One per assignment. A draft until submitted_at is set."""
    assignment = models.OneToOneField(JudgeAssignment, on_delete=models.CASCADE, related_name="review")
    comment = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class Score(models.Model):
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="scores")
    # RESTRICT, not PROTECT: deleting a whole event may take its scores and
    # criteria together, but a scored criterion can't be deleted alone.
    criterion = models.ForeignKey(RubricCriterion, on_delete=models.RESTRICT)
    value = models.SmallIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["review", "criterion"], name="score_unique")]


class CalibrationRun(models.Model):
    """One normalization run, kept whole so a published result can be traced
    back to exactly the scores it read (input_digest)."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="calibration_runs")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    method = models.CharField(max_length=50)
    params = models.JSONField(default=dict)
    input_digest = models.CharField(max_length=64)
    connected = models.BooleanField()
    notes = models.JSONField(default=list)


class CalibratedProject(models.Model):
    run = models.ForeignKey(CalibrationRun, on_delete=models.CASCADE, related_name="projects")
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    n_reviews = models.PositiveSmallIntegerField()
    raw_mean = models.FloatField()
    quality = models.FloatField()
    display = models.FloatField()
    se = models.FloatField()
    rank = models.PositiveIntegerField(null=True)
    raw_rank = models.PositiveIntegerField(null=True)
    excluded = models.CharField(max_length=50, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "project"], name="calibrated_project_unique")]


class JudgeCalibration(models.Model):
    class Flag(models.TextChoices):
        OK = "ok"
        CONSTANT = "constant"
        SINGLE_REVIEW = "single_review"

    run = models.ForeignKey(CalibrationRun, on_delete=models.CASCADE, related_name="judges")
    judge = models.ForeignKey(User, on_delete=models.CASCADE)
    n_reviews = models.PositiveSmallIntegerField()
    offset = models.FloatField()
    scale = models.FloatField()
    noise = models.FloatField()
    flag = models.CharField(max_length=20, choices=Flag.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["run", "judge"], name="judge_calibration_unique")]


class AuditLog(models.Model):
    """Append only. A trigger refuses UPDATE and DELETE and fills prev_hash and
    row_hash, so every row commits to the whole history before it."""
    ts = models.DateTimeField(db_default=Now())
    actor = models.ForeignKey(User, on_delete=models.DO_NOTHING, null=True, blank=True, db_constraint=False, related_name="+")
    event = models.ForeignKey(Event, on_delete=models.DO_NOTHING, null=True, blank=True, db_constraint=False, related_name="+")
    action = models.CharField(max_length=100)
    object_type = models.CharField(max_length=50, blank=True)
    object_id = models.CharField(max_length=100, blank=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    row_hash = models.CharField(max_length=64, blank=True)
    # Chain position, given out by the trigger under a lock. The primary key
    # can't be the chain order: ids are handed out before the lock is taken.
    seq = models.BigIntegerField(unique=True, null=True, blank=True)

    class Meta:
        ordering = ["-seq"]
        indexes = [models.Index(fields=["event", "action"])]


class RoleInvite(models.Model):
    """A single-use link that makes whoever opens it a judge or organizer of
    one event. Organizers hand it out; only its hash is stored."""
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="role_invites")
    role = models.CharField(max_length=20, choices=[(Membership.Role.JUDGE, "judge"),
                                                    (Membership.Role.ORGANIZER, "organizer")])
    note = models.CharField(max_length=200, blank=True, help_text="who it is for, for your own records")
    tracks = models.ManyToManyField(Track, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(used_by__isnull=True) | Q(used_at__isnull=False),
                                              name="role_invite_used_has_time")]
