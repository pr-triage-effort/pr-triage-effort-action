import os
import json
import time

from operator import itemgetter
from dotenv import load_dotenv
from github import Github, Auth

from db.db import Session, init_db
from features.extractor import Extractor
from ml_model import Analyzer
from utils import time_exec

PR_OFFSET = 0

def analysis_script():
    start_time = time.time()

    # Extract Env vars
    load_dotenv(override=True)
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPO")
    cache_reset = os.environ.get("RESET_CACHE")


    # APIs
    auth = Auth.Token(token)
    github_api = Github(auth=auth)

    # Modules
    extractor = Extractor(github_api, repo)

    #DB
    init_db(cache_reset == 'true')

    step_time = time_exec(start_time, "Init")


    # Extract Features
    features = []
    analysis_cnt = 1

    pull_requests = github_api.get_repo(full_name_or_id=repo).get_pulls(state="open")
    for pull in pull_requests:

        if analysis_cnt < PR_OFFSET:
            analysis_cnt += 1
            continue

        pr_feats = extractor.extract_features(pull, analysis_cnt)
        features.append(pr_feats)
        analysis_cnt += 1

    step_time = time_exec(step_time, "Feature extract")


    # Analyse with ML Model
    analyzer = Analyzer()
    for fts in features:
        fts = list(fts.values())
    results = analyzer.analyze_prs(features)
    print(results)

    step_time = time_exec(step_time, "ML Analysis")


    # Write ordered prs to list
    ordered_prs = []
    for i in range(len(results)):
        ordered_prs.append(
            {
                "id": pull_requests[i].id,
                "title": pull_requests[i].title,
                "effort": results[i],
            }
        )
    ordered_prs = sorted(ordered_prs, key=itemgetter("effort"))


    # Write to file
    write_to_json(ordered_prs, "./results.json")
    # write_to_json(extractor.get_cache_(), "./cache.json")

    time_exec(step_time, "Create artefact")
    print(f"Analysis script on \"{repo}\" took {round((time.time() - start_time), 3)} sec to run")


# def post_effort_reviews(prs: PaginatedList[PullRequest], effort: list):
#     pass

def write_to_json(data: list, path: str):
    with open(path, "w", encoding="utf-8") as output_file:
        json.dump(data, output_file, indent=2)

def find_first(lst: list, attr: str, value):
    result = None
    for item in lst:
        if item[attr] == value:
            result = item
            break
    return result

def get_review_count_per_user(closed_prs_last_sixty) -> dict:
    reviewers = {}
    for pr in closed_prs_last_sixty:
        # Find reviewers for current PR
        all_reviewers = []
        for review in pr.get_reviews():
            all_reviewers.append(review.user.login)
        for review_req in pr.get_review_requests()[0]:
            all_reviewers.append(review_req.login)
        for curr_review in all_reviewers:
            reviewers[curr_review] = reviewers.get(curr_review, 0) + 1
    return reviewers

analysis_script()