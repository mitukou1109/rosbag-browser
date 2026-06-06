# rosbag-browser

rosbagを管理・検索するアプリ

## 機能

- `/bags` でbag一覧を表示
  - Scanで手動インデックス更新
  - bagの破損をチェック（現状はファイルの欠損のみ確認）
  - トピック名、キーワード（bag名、メモ）、タグ、記録開始時刻で検索
- `/bags/{id}` で概要とトピック一覧を表示
  - bagごとのメモ・タグ編集

## ローカルでのテスト

```bash
mkdir -p /tmp/rosbag-browser/bags /tmp/rosbag-browser/data
docker compose build rosbag-browser
docker compose up
```

上記実行後、ブラウザで `http://localhost:8000` を開いてください。
Python、HTML、CSSの変更はrebuildなしで反映されます。
依存関係、`Dockerfile`、`pyproject.toml` を変更した場合は再度ビルドしてください。

テストは以下のコマンドで実行できます。

```bash
docker compose run --rm rosbag-browser pytest
```
