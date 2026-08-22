"""
其他平台适配器模板
复制此文件，修改选择器即可适配新平台
"""

# === 猎聘 (liepin.com) ===
# 注册后在 scraper.py 中切换
LIEPIN_SELECTORS = {
    "job_cards": "[class*='job-list-item'], [class*='job-card']",
    "job_name": "[class*='job-title'], h3",
    "company": "[class*='company-name']",
    "salary": "[class*='job-salary'], [class*='salary']",
    "location": "[class*='job-area']",
    "next_page": "[class*='next'], [class*='pagination'] .next",
}


# === 智联招聘 (zhaopin.com) ===
ZHAOPIN_SELECTORS = {
    "job_cards": "[class*='joblist-box__item'], [class*='positionlist'] li",
    "job_name": "[class*='iteminfo__line1__jobname'], h3",
    "company": "[class*='iteminfo__line1__compname']",
    "salary": "[class*='iteminfo__line2__jobdesc__salary']",
    "location": "[class*='iteminfo__line2__jobdesc__demand'] li:first-child",
    "next_page": "[class*='btn-next'], [class*='next']",
}


# === 51job (51job.com) ===
JOB51_SELECTORS = {
    "job_cards": "[class*='j_joblist'] .joblist-item, [class*='joblist'] li",
    "job_name": "[class*='jname'], h3",
    "company": "[class*='cname'], [class*='company-name']",
    "salary": "[class*='sal']",
    "location": "[class*='d at']",
    "next_page": "[class*='next'], [class*='btn-next']",
}


# === 如何添加新平台 ===
# 1. 打开目标网站的搜索结果页
# 2. F12 找到简历卡片、标题、公司、薪资等元素的 CSS 选择器
# 3. 填入上面的字典
# 4. 在 scraper.py 中添加对应的 if 分支
