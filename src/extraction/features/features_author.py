import time
from datetime import datetime, timedelta, timezone
from statistics import median

from github import Github
from github.NamedUser import NamedUser
from github.PullRequest import PullRequest
from github.Repository import Repository
from db.db import Session, User, db_get_user

from features.user_utils import is_bot_user, is_user_reviewer, try_get_total_prs, try_get_reviews_num

DEFAULT_MERGE_RATIO = 0.5
# DATA_AGE_CUTOFF = timedelta(days=1)

def author_features(pr: PullRequest, api: Github, cache: dict):
    user_type = 'public'
    experience = None
    change_number = None
    review_number = None
    changes_per_week = None
    global_merge_ratio = None
    project_merge_ratio = None

    # Author username
    author = pr.user
    author_name = author.login

    # Try retrieve from cache
    with Session() as session:
        db_user = db_get_user(author_name, session)

    if db_user is not None:
        experience = db_user.experience
        change_number = db_user.total_change_number
        review_number = db_user.review_number
        changes_per_week = db_user.changes_per_week
        global_merge_ratio = db_user.global_merge_ratio
        project_merge_ratio = db_user.project_merge_ratio

        # If present, return results
        if None not in (experience, change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio):
            # if author_cache['last_update'] < (datetime.now(timezone.utc) - DATA_AGE_CUTOFF):
            return {
                'author_experience': experience,
                'total_change_num': change_number,
                'author_review_num': review_number,
                'author_changes_per_week': changes_per_week,
                'author_merge_ratio': global_merge_ratio,
                'author_merge_ratio_in_project': project_merge_ratio
            }

    # 60-day window
    now = datetime.now(timezone.utc)
    sixty_days_ago = now - timedelta(days=60)

    # Author experience
    if experience is None:
        registration_date = author.created_at
        latest_revision = pr.created_at
        experience = (latest_revision.date() - registration_date.date()).days / 365.25

    if is_bot_user(author, pr.base.repo.full_name):
        change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio = bot_user_features(author, pr.base.repo, sixty_days_ago)
        user_type = 'bot'

    else:
        # Author total change number (Will throw 422 and return None of user private)
        change_number = try_get_total_prs(author, api)

        # User with private profile
        if change_number is None:
            change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio = private_user_features(author, pr.base.repo, sixty_days_ago, cache.get('users'))
            user_type = 'private'

        else:
            # Author review number
            if review_number is None:
                review_number = try_get_reviews_num(author_name, sixty_days_ago, now, api)

            # Author changes per week
            global_pr_closed = api.search_issues(f"author:{author_name} type:pr is:closed closed:{sixty_days_ago.date()}..{now.date()}").totalCount
            changes_per_week = global_pr_closed * (7/60)

            # Author global merge ratio
            if global_pr_closed == 0:
                global_merge_ratio = DEFAULT_MERGE_RATIO
                project_merge_ratio = DEFAULT_MERGE_RATIO
            else:
                global_pr_merged = api.search_issues(f"author:{author_name} type:pr is:merged merged:{sixty_days_ago.date()}..{now.date()}").totalCount
                global_merge_ratio = global_pr_merged /global_pr_closed

                # Author project merge ratio
                repo_pulls = pr.base.repo.get_pulls(state='closed')
                proj_closed_pulls = 0
                proj_merged_pulls = 0

                for pull in repo_pulls:
                    if pull.closed_at < sixty_days_ago:
                        break
                    if pull.user.login == author_name:
                        proj_closed_pulls += 1
                        if pull.merged:
                            proj_merged_pulls += 1

                if proj_closed_pulls == 0:
                    project_merge_ratio = DEFAULT_MERGE_RATIO
                else:
                    project_merge_ratio = proj_merged_pulls / proj_closed_pulls

    # Cache results
    with Session() as session:
        db_user = db_get_user(author_name, session)

        if db_user is None:
            db_user = User(
                username = author_name,
                tag = 'full',
                type = user_type,
                experience = experience,
                total_change_number = change_number,
                review_number = review_number,
                changes_per_week = changes_per_week,
                global_merge_ratio = global_merge_ratio,
                project_merge_ratio = project_merge_ratio
            )
            session.add(db_user)
        else:
            db_user.tag = 'full'
            db_user.type = user_type
            db_user.experience = experience
            db_user.total_change_number = change_number
            db_user.review_number = review_number
            db_user.changes_per_week = changes_per_week
            db_user.global_merge_ratio = global_merge_ratio
            db_user.project_merge_ratio = project_merge_ratio
  
        session.commit()

    # Cache last update timestamp
    # cache['users'][author_name]['last_update'] = datetime.now(timezone.utc)

    return {
            'author_experience': experience,
            'total_change_num': change_number,
            'author_review_num': review_number,
            'author_changes_per_week': changes_per_week,
            'author_merge_ratio': global_merge_ratio,
            'author_merge_ratio_in_project': project_merge_ratio
        }


def bot_user_features(user: NamedUser, repo: Repository, time_limit: datetime):
    change_number = 0
    review_number = 0
    closed_prs = 0
    merged_prs = 0

    prs = repo.get_pulls()
    for pr in prs:
        # Change number
        if pr.user.login == user.login:
            change_number += 1

        if pr.state == 'closed' and pr.closed_at >= time_limit:
            if is_user_reviewer(pr, user):
                review_number += 1
            elif pr.user.login == user.login:
                closed_prs += 1
                if pr.merged:
                    merged_prs += 1

    if closed_prs > 0:
        changes_per_week = closed_prs * (7/60)
        project_merge_ratio = merged_prs / closed_prs
    else:
        changes_per_week = 0
        project_merge_ratio = DEFAULT_MERGE_RATIO

    global_merge_ratio = project_merge_ratio

    return [change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio]

def private_user_features(user: NamedUser, repo: Repository, time_limit: datetime, author_cache: dict):
    # Merge ratios
    closed_pr_num = 0
    merged_pr_num = 0

    prs = repo.get_pulls(state='closed')
    for pr in prs:
        if pr.closed_at < time_limit:
            break

        if pr.user.login == user.login:
            closed_pr_num += 1
            if pr.merged:
                merged_pr_num += 1

    global_merge_ratio = DEFAULT_MERGE_RATIO
    if closed_pr_num > 0:
        project_merge_ratio = merged_pr_num / closed_pr_num
    else:
        project_merge_ratio = DEFAULT_MERGE_RATIO

    # Median of other authors
    change_number = []
    review_number = []
    changes_per_week = []

    with Session() as session:
        authors = session.query(User).all()
    
    # TODO Need to push private user analysis to the end of queue
    if len(authors) > 0:
        for author in authors:
            if author.tag == 'full':
                change_number.append(author.total_change_number)
                review_number.append(author.review_number)
                changes_per_week.append(author.changes_per_week)

        change_number = median(change_number) if len(change_number) > 0 else 0
        review_number = median(review_number) if len(review_number) > 0 else 0
        changes_per_week = median(changes_per_week) if len(changes_per_week) > 0 else 0
    else:
        change_number = 0
        review_number = 0
        changes_per_week = 0

    
 
    return [change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio]
