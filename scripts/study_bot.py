"""CECOM DL Study bot : Notion x GitHub x Discord
usage: python study_bot.py open | close | rollover | remind | activity | share <paths...>
env: NOTION_TOKEN, DASHBOARD, MEMBER_DB, RECORD_DB, STUDY_DB, CURRICULUM_DB, DISCORD_WEBHOOK
"""
import os, sys, math, random, datetime as dt, requests

API = "https://api.notion.com/v1"
H = {"Authorization": f"Bearer {os.environ['NOTION_TOKEN']}",
     "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
ENV = os.environ.get
KST = dt.timezone(dt.timedelta(hours=9))
# 모임 날짜 (1주차 = OT). 10월은 쉼
SESSIONS = [dt.date(2026, 9, 21), dt.date(2026, 9, 28), dt.date(2026, 11, 2),
            dt.date(2026, 11, 9), dt.date(2026, 11, 16), dt.date(2026, 11, 23)]
LAST = len(SESSIONS)
PLACE = "301관 B203호"
CHECKS = ["출석", "지각", "사유결석"]  # 앞쪽 우선, 아무것도 없으면 무단결석


# Notion helpers
def week_of(d):
    """모임 날이면 주차, 아니면 None"""
    return SESSIONS.index(d) + 1 if d in SESSIONS else None


def query(db, flt=None, sorts=None):
    body, out = {}, []
    if flt:
