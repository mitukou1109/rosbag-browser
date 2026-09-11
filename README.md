# rosbag-browser

rosbagを管理・検索するアプリ

実行には[`uv`](https://github.com/astral-sh/uv)が必要です。

## 機能

- `/bags` でbag一覧を表示
  - ローカル起動時は任意のルートディレクトリを選択
  - Scanで手動インデックス更新
  - bagの破損をチェック（現状はファイルの欠損のみ確認）
  - トピック名、キーワード（bag名、メモ）、タグ、記録開始時刻で検索
- `/bags/{id}` で概要とトピック一覧を表示
  - bagごとのメモ・タグ編集
  - bagをzipでダウンロード

## デスクトップアプリで使う

```bash
cd /path/to/rosbag-browser
uv sync
uv run rosbag-browser
```

## ブラウザで使う

```bash
uv run rosbag-browser-server --reload
```

ブラウザで `http://localhost:8000` を開いてください。ポートを変更する場合は `--port 8001` のように指定できます。

## Dockerで使う

NASなどサーバーで運用する場合は、ルートディレクトリ（既定は `/share/Bags`）を `HOST_BAG_ROOT` で指定して起動します。

```bash
docker compose build rosbag-browser
HOST_BAG_ROOT=/path/to/Bags docker compose up
```

上記実行後、ブラウザで `http://<your-host>:8000` を開いてください。

## テスト

テストは以下のコマンドで実行できます。

```bash
docker compose run --rm rosbag-browser pytest
```
