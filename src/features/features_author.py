import functools
import collections
import operator

from datetime import datetime, timedelta, timezone
from statistics import median
import time

from github import Github, GithubException
from github.NamedUser import NamedUser
from github.PullRequest import PullRequest
from github.Repository import Repository

from features.user_utils import is_bot_user, is_user_reviewer, try_get_total_prs

DEFAULT_MERGE_RATIO = 0.5
# DATA_AGE_CUTOFF = timedelta(days=1)

def author_features(pr: PullRequest, api: Github, cache: dict, diff_user: NamedUser = None):
    user_type = 'public'

    # Author username
    author = pr.user
    if diff_user is not None:
        author = diff_user
    author_name = author.login

    # Try retrieve from cache
    author_cache = cache.get('users', {}).get(author_name, {})
    experience = author_cache.get('total_change_number', None)
    change_number = author_cache.get('total_change_number', None)
    review_number = author_cache.get('author_review_number', None)
    changes_per_week = author_cache.get('author_changes_per_week', None)
    global_merge_ratio = author_cache.get('author_merge_ratio', None)
    project_merge_ratio = author_cache.get('author_merge_ratio_in_project', None)
    
    # If present, return results
    if None not in (experience, change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio):
        # if author_cache['last_update'] < (datetime.now(timezone.utc) - DATA_AGE_CUTOFF):
        return {
            'author_experience': experience,
            'total_change_number': change_number,
            'author_review_number': review_number,
            'author_changes_per_week': changes_per_week,
            'author_merge_ratio': global_merge_ratio,
            'author_merge_ratio_in_project': project_merge_ratio
        }

    # 60-day window
    now = datetime.now(timezone.utc)
    sixty_days_ago = now - timedelta(days=60)

    # Author experience
    registration_date = author.created_at
    latest_revision = pr.created_at
    experience = (latest_revision.date() - registration_date.date()).days / 365.25

    if is_bot_user(author_name, pr.base.repo.full_name):
        change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio = bot_user_features(author, pr.base.repo, sixty_days_ago)
        user_type = 'bot'

    else:
        # Author total change number
        change_number = try_get_total_prs(author, api)

        # User with private profile
        if change_number is None:
            change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio = private_user_features(author, pr.base.repo, sixty_days_ago, cache.get('users'))
            user_type = 'private'
            
        else:
            # Author review number
            review_number = api.search_issues(f"type:pr reviewed-by:{author_name} closed:{sixty_days_ago.date()}..{now.date()}").totalCount
            review_number += api.search_issues(f"type:pr review-requested:{author_name} closed:{sixty_days_ago.date()}..{now.date()}").totalCount

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
                project_pr_closed = api.search_issues(f"author:{author_name} repo:{pr.base.repo.full_name} type:pr is:closed closed:{sixty_days_ago.date()}..{now.date()}").totalCount
                if project_pr_closed == 0:
                    project_merge_ratio = DEFAULT_MERGE_RATIO
                else:
                    project_pr_merged = api.search_issues(f"author:{author_name} repo:{pr.base.repo.full_name} type:pr is:merged merged:{sixty_days_ago.date()}..{now.date()}").totalCount
                    project_merge_ratio = project_pr_merged / project_pr_closed

    # Cache results
    if author not in cache['users']:
        cache['users'][author_name] = {}

    cache['users'][author_name]['type'] = user_type
    cache['users'][author_name]['author_experience'] = experience
    cache['users'][author_name]['total_change_number'] = change_number
    cache['users'][author_name]['author_review_number'] = review_number
    cache['users'][author_name]['author_changes_per_week'] = changes_per_week
    cache['users'][author_name]['author_merge_ratio'] = global_merge_ratio
    cache['users'][author_name]['author_merge_ratio_in_project'] = project_merge_ratio

    # Cache last update timestamp
    # cache['users'][author_name]['last_update'] = datetime.now(timezone.utc)

    return {
            'author_experience': experience,
            'total_change_number': change_number,
            'author_review_number': review_number,
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
        if pr.created_at < user.created_at:
            break

        if pr.user.login == user.login:
            change_number += 1

        if pr.state == 'closed' and pr.closed_at >= time_limit:
            if pr.user.login == user.login:
                closed_prs += 1
                if pr.merged:
                    merged_prs += 1

            elif is_user_reviewer(pr, user):
                review_number += 1

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
    start = time.time()

    change_number = []
    review_number = []
    changes_per_week = []

    for author in author_cache.values():
        change_number.append(author['total_change_number'])
        review_number.append(author['author_review_number'])
        changes_per_week.append(author['author_changes_per_week'])

    change_number = median(change_number)
    review_number = median(review_number)
    changes_per_week = median(changes_per_week)

    return [change_number, review_number, changes_per_week, global_merge_ratio, project_merge_ratio]