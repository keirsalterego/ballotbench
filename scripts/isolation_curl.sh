#!/bin/sh
# Try to get at things you shouldn't, over HTTP, against the running stack.
# Every attempt must be refused with the status shown. Exit 1 on any miss.
#   sh scripts/isolation_curl.sh [base_url]
set -u
BASE=${1:-http://localhost:8080}
EV=sample-hack-2026
ORG="Authorization: Bearer bb_demo_organizer_5c1e0a"
JA="Authorization: Bearer bb_demo_judge_a_8d24f1"
JB="Authorization: Bearer bb_demo_judge_b_3a9e77"
PT="Authorization: Bearer bb_demo_participant_61b0c4"
fails=0
total=0

# expect STATUS LABEL METHOD PATH [HEADER] [BODY]
expect() {
  want=$1 label=$2 method=$3 path=$4 header=${5:-} body=${6:-}
  total=$((total + 1))
  if [ -n "$body" ]; then
    got=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" ${header:+-H "$header"} \
          -H 'Content-Type: application/json' -d "$body" "$BASE$path")
  else
    got=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" ${header:+-H "$header"} "$BASE$path")
  fi
  if [ "$got" = "$want" ]; then
    echo "ok   $got $label"
  else
    echo "FAIL $got (wanted $want) $label: $method $path"
    fails=$((fails + 1))
  fi
}

# Ids we need, read with the owners' own tokens.
first_id() { sed -n 's/^\[{"\(id\|assignment\)":\([0-9]*\).*/\2/p'; }
A_ASSIGNMENT=$(curl -s -H "$JA" "$BASE/api/judge/assignments" | first_id)
A_PROJECT=$(curl -s -H "$JA" "$BASE/api/judge/scores" | sed -n 's/.*"project":\([0-9]*\).*/\1/p' | head -1)
OWN_PROJECT=$(curl -s "$BASE/api/events/$EV/projects" | sed -n 's/.*"id":\([0-9]*\),"event":"[^"]*","team":"NorthKiln".*/\1/p' | head -1)
OTHER_PROJECT=$(curl -s "$BASE/api/events/$EV/projects" | sed -n 's/.*"id":\([0-9]*\),"event":"[^"]*","team":"LoudQuarry".*/\1/p' | head -1)

echo "== judge scores"
expect 200 "judge_a reads own scores"                      GET "/api/judge/scores" "$JA"
expect 403 "judge_b names judge_a by fixture id"          GET "/api/judge/scores?judge=jdg_24" "$JB"
expect 403 "judge_b names judge_a by email"               GET "/api/judge/scores?judge=diego.herrera@example.org" "$JB"
expect 403 "judge_b names the constant judge"             GET "/api/judge/scores?judge=jdg_07" "$JB"
expect 403 "participant reads judge scores"               GET "/api/judge/scores" "$PT"
expect 401 "anonymous reads judge scores"                 GET "/api/judge/scores"
expect 401 "a made-up token"                              GET "/api/judge/scores" "Authorization: Bearer bb_guess"
expect 403 "participant lists assignments"                GET "/api/judge/assignments" "$PT"

echo "== someone else's review"
expect 404 "judge_b scores judge_a's assignment"          POST "/api/judge/assignments/$A_ASSIGNMENT/review" "$JB" '{"scores":{"quality":5}}'
expect 404 "participant scores judge_a's assignment"      POST "/api/judge/assignments/$A_ASSIGNMENT/review" "$PT" '{"scores":{"quality":5}}'
expect 404 "judge_b opens judge_a's scoresheet page"       GET "/judge/assignments/$A_ASSIGNMENT" "$JB"

echo "== deadline"
expect 409 "participant submits after the close"          POST "/api/events/$EV/projects" "$PT" '{"title":"late","summary":"x"}'
expect 409 "participant edits own project after close"    PATCH "/api/projects/$OWN_PROJECT" "$PT" '{"title":"late"}'
expect 403 "participant edits another team's project"     PATCH "/api/projects/$OTHER_PROJECT" "$PT" '{"title":"mine"}'
expect 403 "judge creates a project"                      POST "/api/events/$EV/projects" "$JA" '{"title":"x","summary":"x"}'
expect 401 "anonymous creates a project"                  POST "/api/events/$EV/projects" "" '{"title":"x","summary":"x"}'

echo "== exports"
for kind in scores results judges audit registrations teams projects assignments; do
  expect 401 "anonymous exports $kind"                    GET "/api/events/$EV/export/$kind.csv"
  expect 403 "participant exports $kind"                  GET "/api/events/$EV/export/$kind.csv" "$PT"
  expect 403 "judge exports $kind"                        GET "/api/events/$EV/export/$kind.csv" "$JA"
  expect 200 "organizer exports $kind"                    GET "/api/events/$EV/export/$kind.csv" "$ORG"
done

echo "== results before publication"
expect 404 "anonymous reads unpublished results"          GET "/api/events/$EV/results"
expect 404 "judge reads unpublished results"              GET "/api/events/$EV/results" "$JA"
expect 404 "participant reads unpublished results"        GET "/api/events/$EV/results" "$PT"

echo "== organizer pages with a token that isn't an organizer's"
for page in manage manage/assign manage/progress manage/calibration manage/audit manage/duplicates manage/exports; do
  expect 403 "judge opens $page"                          GET "/events/$EV/$page" "$JA"
  expect 403 "participant opens $page"                    GET "/events/$EV/$page" "$PT"
done

echo "== signed records, bundles and webhooks (tier T4)"
expect 200 "judge_a fetches own signed record"            GET "/api/judge/record?event=$EV" "$JA"
expect 403 "judge_b fetches judge_a's record by id"       GET "/api/judge/record?event=$EV&judge=jdg_24" "$JB"
expect 403 "judge_b fetches judge_a's record by email"    GET "/api/judge/record?event=$EV&judge=diego.herrera@example.org" "$JB"
expect 403 "participant fetches a judge record"           GET "/api/judge/record?event=$EV" "$PT"
expect 401 "anonymous fetches a judge record"             GET "/api/judge/record?event=$EV"
expect 404 "judge_b opens judge_a's certificate"          GET "/events/$EV/certificate?person=diego.herrera@example.org" "$JB"
expect 401 "anonymous exports the event bundle"           GET "/api/events/$EV/export/bundle.json"
expect 403 "participant exports the event bundle"         GET "/api/events/$EV/export/bundle.json" "$PT"
expect 403 "judge exports the event bundle"               GET "/api/events/$EV/export/bundle.json" "$JA"
expect 403 "participant imports an event"                 POST "/api/events/import?slug=mine" "$PT" '{}'
expect 401 "anonymous imports an event"                   POST "/api/events/import?slug=mine" "" '{}'
expect 403 "participant opens manage/webhooks"            GET "/events/$EV/manage/webhooks" "$PT"
expect 403 "judge opens manage/webhooks"                  GET "/events/$EV/manage/webhooks" "$JA"

echo "== public pages stay public"
expect 200 "gallery"                                      GET "/projects"
expect 200 "a submitted project"                          GET "/projects/$A_PROJECT"
expect 200 "api schema"                                   GET "/api/schema"
expect 200 "the signing key"                              GET "/.well-known/ballotbench-signing-key"
expect 200 "the embeddable gallery"                       GET "/embed/$EV"

echo
echo "$((total - fails)) of $total as expected"
[ "$fails" -eq 0 ]
