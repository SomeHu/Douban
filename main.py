import requests
from bs4 import BeautifulSoup
import re
import os
import json

# 从环境变量获取配置
DOUBAN_USERNAME = os.environ['DOUBAN_USERNAME']
NOTION_TOKEN = os.environ['NOTION_TOKEN']
NOTION_DATABASE_ID = os.environ['NOTION_DATABASE_ID']

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
    'Notion-Version': '2022-06-28',
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {NOTION_TOKEN}'
}

STATUS_MAP = {
    'wish': '想看',
    'do': '正在看',
    'collect': '看过'
}

def get_user_movies(username, status):
    movies = []
    start = 0
    while True:
        url = f"https://movie.douban.com/people/{username}/{status}?start={start}&sort=time&rating=all&filter=all&mode=grid"
        resp = requests.get(url, headers={'User-Agent': HEADERS['User-Agent']})
        if resp.status_code != 200:
            print(f"Failed to fetch {url}")
            break
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.find_all('div', class_='item')
        if not items:
            break
        for item in items:
            link_elem = item.find('a', class_='nbg')
            if not link_elem:
                continue
            link = link_elem['href']
            title_full = item.find('em').text.strip()
            simplified_title = ' '.join(re.findall(r'[\u4e00-\u9fa5]+', title_full))
            film_id = link.split('/')[-2]
            details = get_movie_details(link)
            movies.append({
                'id': film_id,
                'title': simplified_title or title_full,  # 如果无中文，用原标题
                'type': details['type'],
                'release_date': details['release_date'],
                'region': details['region'],
                'status': STATUS_MAP[status]
            })
        start += 15
    return movies

def get_movie_details(url):
    resp = requests.get(url, headers={'User-Agent': HEADERS['User-Agent']})
    soup = BeautifulSoup(resp.text, 'html.parser')
    info = soup.find('div', id='info')
    if not info:
        return {'type': '', 'release_date': '', 'region': ''}
    
    # 类型
    genres = [g.text.strip() for g in info.find_all('span', property='v:genre')]
    type_ = '/'.join(genres)
    
    # 首播日期（取第一个）
    release_elem = info.find('span', property='v:initialReleaseDate')
    release_date = release_elem.text.split('(')[0].strip() if release_elem else ''
    
    # 地区（从文本提取）
    region_match = re.search(r'制片国家/地区:</span>\s*(.*?)<br', str(info), re.DOTALL)
    region = region_match.group(1).strip().split('/')[0].strip() if region_match else ''  # 取第一个地区
    
    return {'type': type_, 'release_date': release_date, 'region': region}

def get_existing_movies(db_id):
    url = f"https://api.notion.com/v1/databases/{db_id}/query"
    resp = requests.post(url, headers=HEADERS)
    if resp.status_code != 200:
        print(f"Failed to query Notion: {resp.text}")
        return {}
    pages = resp.json().get('results', [])
    existing = {}
    for page in pages:
        props = page['properties']
        film_id_prop = props.get('Film ID', {}).get('rich_text', [])
        if film_id_prop:
            film_id = film_id_prop[0].get('text', {}).get('content', '')
            status_prop = props.get('Status', {}).get('select', {}).get('name', '')
            existing[film_id] = {'page_id': page['id'], 'current_status': status_prop}
    return existing

def sync_to_notion(movies, existing):
    for movie in movies:
        film_id = movie['id']
        if film_id in existing:
            # 更新（仅如果状态变化）
            current_status = existing[film_id]['current_status']
            if current_status != movie['status']:
                page_id = existing[film_id]['page_id']
                payload = {
                    "properties": {
                        "Status": {"select": {"name": movie['status']}}
                    }
                }
                url = f"https://api.notion.com/v1/pages/{page_id}"
                resp = requests.patch(url, headers=HEADERS, json=payload)
                print(f"Updated {movie['title']}: {resp.status_code}")
        else:
            # 添加新条目
            payload = {
                "parent": {"database_id": NOTION_DATABASE_ID},
                "properties": {
                    "Title": {"title": [{"text": {"content": movie['title']}}]},
                    "类型": {"multi_select": [{"name": g.strip()} for g in movie['type'].split('/') if g.strip()]},
                    "首播日期": {"date": {"start": movie['release_date'] or None}},
                    "地区": {"select": {"name": movie['region'] or None}},
                    "Film ID": {"rich_text": [{"text": {"content": film_id}}]},
                    "Status": {"select": {"name": movie['status']}}
                }
            }
            url = "https://api.notion.com/v1/pages"
            resp = requests.post(url, headers=HEADERS, json=payload)
            print(f"Added {movie['title']}: {resp.status_code}")

if __name__ == "__main__":
    all_movies = []
    for status in ['wish', 'do', 'collect']:
        all_movies.extend(get_user_movies(DOUBAN_USERNAME, status))
    existing = get_existing_movies(NOTION_DATABASE_ID)
    sync_to_notion(all_movies, existing)
