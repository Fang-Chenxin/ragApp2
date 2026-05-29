#!/usr/bin/env python3
"""快速生成变更摘要文档并可选更新 CHANGELOG.md

用法示例:
python tools/create_change_summary.py --title "后端商品搜索服务优化" --date 2026-05-29 \
  --type Changed --files backend/service/sqlite_product_query_tool.py,backend/service/sqlite_product_search_service.py \
  --description "优化商品搜索降级逻辑与属性过滤器规范" --slug 后端商品搜索服务优化 --update-changelog --version v1.0.1

默认仅创建文档草稿并在 stdout 打印 changelog 片段；加上 --update-changelog 会直接修改 `CHANGELOG.md`（会自动备份为 `CHANGELOG.md.bak`）。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import datetime


TEMPLATE = """# {title}

## 变更时间
{date}

## 变更概述
{description}

---

## 后端变更

### 主要修改文件
{files_table}

---

## 用户使用说明

请参阅项目 `CHANGELOG.md` 以关联版本号与发布日期。
"""


def slugify(name: str) -> str:
    return name.strip().replace(' ', '').replace('/', '_')


def build_files_table(files_csv: str) -> str:
    files = [f.strip() for f in files_csv.split(',') if f.strip()]
    if not files:
        return '- 无'
    rows = '\n'.join([f"- `{f}`" for f in files])
    return rows


def generate_markdown(title: str, date: str, description: str, files_csv: str) -> str:
    return TEMPLATE.format(
        title=title,
        date=date,
        description=description,
        files_table=build_files_table(files_csv)
    )


def make_changelog_snippet(version: str, date: str, change_type: str, title: str, doc_path: str) -> str:
    return f"## [{version}] - {date}\n\n### {change_type}\n- {title} - [详细文档]({doc_path})\n\n"


def update_changelog_file(snippet: str, changelog_path: Path) -> None:
    backup = changelog_path.with_suffix('.md.bak')
    if changelog_path.exists():
        changelog_path.replace(backup)
        existing = backup.read_text(encoding='utf-8')
    else:
        existing = '# Changelog\n\n'

    new_content = '# Changelog\n\n' + snippet + existing
    changelog_path.write_text(new_content, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--title', required=True)
    parser.add_argument('--date', required=False)
    parser.add_argument('--description', required=True)
    parser.add_argument('--files', required=False, default='')
    parser.add_argument('--slug', required=False)
    parser.add_argument('--update-changelog', action='store_true')
    parser.add_argument('--version', required=False)
    parser.add_argument('--type', required=False, default='Changed', choices=['Added','Changed','Fixed'])

    args = parser.parse_args()

    title = args.title
    date = args.date or datetime.date.today().isoformat()
    description = args.description
    files = args.files
    slug = args.slug or slugify(title)

    out_dir = Path('docs/变更摘要')
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slug}.md"
    out_path = out_dir / filename

    md = generate_markdown(title, date, description, files)

    if out_path.exists():
        print(f"警告: {out_path} 已存在，脚本将不覆盖。请手动合并或删除后重新运行。")
        return

    out_path.write_text(md, encoding='utf-8')
    print(f"已创建: {out_path}")

    doc_rel_path = f"docs/变更摘要/{filename}"
    snippet = ''
    if args.version:
        snippet = make_changelog_snippet(args.version, date, args.type, title, doc_rel_path)
        print('\n=== CHANGELOG 片段（可直接粘贴或使用 --update-changelog 写入）===\n')
        print(snippet)

    if args.update_changelog:
        changelog_path = Path('CHANGELOG.md')
        update_changelog_file(snippet, changelog_path)
        print(f"已更新 {changelog_path}（备份保存在 {changelog_path.with_suffix('.md.bak')}）")


if __name__ == '__main__':
    main()
