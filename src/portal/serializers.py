from rest_framework import serializers

from .models import Event, Project, Review, RubricCriterion, Track


class TrackSerializer(serializers.ModelSerializer):
    class Meta:
        model = Track
        fields = ["id", "name"]


class CriterionSerializer(serializers.ModelSerializer):
    class Meta:
        model = RubricCriterion
        fields = ["key", "name", "weight", "min_value", "max_value"]


class EventSerializer(serializers.ModelSerializer):
    tracks = TrackSerializer(many=True, read_only=True)
    criteria = CriterionSerializer(many=True, read_only=True)

    class Meta:
        model = Event
        fields = ["slug", "name", "description", "submissions_open", "submissions_close", "judging_open",
                  "judging_close", "voting_open", "voting_close", "results_published_at",
                  "reviews_per_project", "max_team_size", "tracks", "criteria"]


class ProjectSerializer(serializers.ModelSerializer):
    event = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    team = serializers.CharField(source="team.name", read_only=True)
    track = serializers.PrimaryKeyRelatedField(queryset=Track.objects.all(), required=False, allow_null=True)
    submit = serializers.BooleanField(write_only=True, required=False, default=False,
                                      help_text="true submits the project; otherwise it is saved as a draft")

    class Meta:
        model = Project
        fields = ["id", "event", "team", "track", "title", "tagline", "summary", "description", "repo_url",
                  "demo_url", "tags", "status", "submitted_at", "duplicate_of", "submit"]
        read_only_fields = ["status", "submitted_at", "duplicate_of"]

    def validate_track(self, track):
        event = self.context["event"]
        if track is not None and track.event_id != event.pk:
            raise serializers.ValidationError("that track is not in this event")
        return track


class ReviewScoresSerializer(serializers.Serializer):
    """One review as the judge who wrote it sees it."""
    assignment = serializers.IntegerField(source="assignment.pk")
    event = serializers.CharField(source="assignment.event.slug")
    project = serializers.IntegerField(source="assignment.project.pk")
    project_title = serializers.CharField(source="assignment.project.title")
    judge = serializers.CharField(source="assignment.judge.email")
    status = serializers.CharField(source="assignment.status")
    submitted_at = serializers.DateTimeField()
    comment = serializers.CharField()
    scores = serializers.SerializerMethodField()
    weighted_total = serializers.SerializerMethodField()

    def get_scores(self, review: Review) -> dict[str, int]:
        return {s.criterion.key: s.value for s in review.scores.all()}

    def get_weighted_total(self, review: Review) -> float | None:
        from .scoring import weighted_score
        return weighted_score(review)
