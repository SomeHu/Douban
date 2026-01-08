import requests
from bs4 import BeautifulSoup
import re
import os
import time
import random

# 配置
DOUBAN_USERNAME = os.environ['DOUBAN_USERNAME']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
NOTION_DATABASE_ID = os.environ['NOTION_DATABASE_ID']

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Referer': 'https://movie.douban.com/',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
}

NOTION_HEADERS = {
    'Authorization': f'Bearer {NOTION_TOKEN}',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
}

STATUS_MAP = {
    'wish': '想看',
    'do': '正在看',
    'collect': '看过'
}

def get_user_movies(username, status):
    movies = []
    start = 0
    print(f"开始获取 {STATUS_MAP[status]} 列表...")
    while True:
        url = f"https://movie.douban.com/people/{username}/{status}?start={start}&sort=time&rating=all&filter=all&mode=grid"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"请求列表失败 {url}: {e}")
            break

        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.find_all('div', class_='item')
        if not items:
            # 额外检查是否被反爬（页面有“验证”或登录提示）
            if "请验证" in resp.text or "登录" in resp.text:
                print("豆瓣检测到爬虫或需要登录，请检查您的收藏是否公开！")
            print(f"{STATUS_MAP[status]} 列表获取完毕（本页无数据）")
            break

        print(f"第 {start // 15 + 1} 页，获取到 {len(items)} 条")
        for item in items:
            link_elem = item.find('a', class_='nbg')
            if not link_elem:
                continue
            link = link_elem['href']
            title_full = item.find('em').text.strip()
            # 优先提取中文
            simplified_title = ' '.join(re.findall(r'[\u4e00-\u9fa5]+', title_full))
            if not simplified_title:
                simplified_title = title_full.split(' / ')[0].strip()
            film_id = link.strip('/').split('/')[-1]

            details = get_movie_details(link)
            movies.append({
                'id': film_id,
                'title': simplified_title,
                'type': details['type'],
                'release_date': details['release_date'],
                'region': details['region'],
                'status': STATUS_MAP[status]
            })

        start += 15
        time.sleep(3 + random.random() * 3)

    print(f"{STATUS_MAP[status]} 共获取 {len(movies)} 条")
    return movies

def get_movie_details(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"详情页失败 {url}: {e}")
        return {'type': '', 'release_date': '', 'region': ''}

    soup = BeautifulSoup(resp.text, 'html.parser')
    info = soup.find('div', id='info')
    if not info:
        return {'type': '', 'release_date': '', 'region': ''}

    # 类型
    genres = [g.text.strip() for g in info.find_all('span', property='v:genre')]
    type_ = '/'.join(genres)

    # 首播日期
    release_elem = info.find('span', property='v:initialReleaseDate')
    release_date = release_elem.text.split('(')[0].strip() if release_elem else ''

    # 地区
    region_text = info.get_text()
    region_match = re.search(r'制片国家/地区:\s*([^\n]+)', region_text)
    region = region_match.group(1).strip().split('/')[0].strip() if region_match else ''

    time.sleep(1 + random.random())
    return {'type': type_, 'release_date': release_date, 'region': region}

def get_existing_movies():
    url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query"
    existing = {}
    next_cursor = None
    while True:
        payload = {"page_size": 100}
        if next_cursor:
            payload["start_cursor"] = next_cursor
        try:
            resp = requests.post(url, headers=NOTION_HEADERS, json=payload, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"Notion 查询失败: {e}")
            break

        data = resp.json()
        for page in data.get('results', []):
            props = page['properties']

            # 安全获取 Film ID
            film_id_prop = props.get('Film ID', {}).get('rich_text', [])
            film_id = film_id_prop[0]['text']['content'] if film_id_prop else ''

            # 安全获取 Status（修复核心 bug）
            status_obj = props.get('Status', {})
            status_name = ''
            if status_obj and status_obj.get('select'):
                status_name = status_obj['select'].get('name', '')

            if film_id:
                existing[film_id] = {
                    'page_id': page['id'],
                    'current_status': status_name
                }

        next_cursor = data.get('next_cursor')
        if not data.get('has_more', False):
            break

    print(f"Notion 中现有 {len(existing)} 条记录")
    return existing

def sync_to_notion(movies, existing):
    added = updated = 0
    for movie in movies:
        film_id = movie['id']
        if film_id in existing:
            if existing[film_id]['current_status'] != movie['status']:
                payload = {
                    "properties": {
                        "Status": {"select": {"name": movie['status']}}
                    }
                }
                url = f"https://api.notion.com/v1/pages/{existing[film_id]['page_id']}"
                resp = requests.patch(url, headers=NOTION_HEADERS, json=payload)
                print(f"更新状态 {movie['title']} -> {movie['status']} ({resp.status_code})")
                updated += 1
        else:
            payload = {
                "parent": {"database_id": NOTION_DATABASE_ID},
                "properties": {
                    "Name": {"title": [{"text": {"content": movie['title']}}]},  # ← 如您的标题属性叫 “Title” 请改为 "Title"
                    "类型": {"multi_select": [{"name": g.strip()} for g in movie['type'].split('/') if g.strip()]},
                    "首播日期": {"date": {"start": movie['release_date']} if movie['release_date'] else None},
                    "地区": {"select": {"name": movie['region']} if movie['region'] else None},
                    "Film ID": {"rich_text": [{"text": {"content": film_id}}]},
                    "Status": {"select": {"name": movie['status']}}
                }
            }
            resp = requests.post("https://api.notion.com/v1/pages", headers=NOTION_HEADERS, json=payload)
            print(f"新增 {movie['title']} ({resp.status_code})")
            added += 1

        time.sleep(0.5)  # Notion 限速

    print(f"同步完成：新增 {added} 条，更新 {updated} 条")

if __name__ == "__main__":
    print("开始豆瓣 → Notion 同步...")
    all_movies = []
    for status in ['wish', 'do', 'collect']:
        all_movies.extend(get_user_movies(DOUBAN_USERNAME, status))

    print(f"豆瓣总获取 {len(all_movies)} 条（去重前）")
    unique_movies = {m['id']: m for m in all_movies}
    all_movies = list(unique_movies.values())
    print(f"去重后 {len(all_movies)} 条")

    existing = get_existing_movies()
    sync_to_notion(all_movies, existing)
    print("全部完成！")
