from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, Union

import requests


class ClickHouseError(RuntimeError):
    pass


def get_dag_queries(path_to_folder_with_sql_queries: str) -> Dict[str, str]:
    sql_files_dict: Dict[str, str] = {}
    for filename in os.listdir(path_to_folder_with_sql_queries):
        if filename.endswith(".sql"):
            with open(
                os.path.join(path_to_folder_with_sql_queries, filename),
                "r",
                encoding="utf-8",
            ) as file:
                sql_files_dict[filename.split(".", 1)[0]] = file.read()
    return sql_files_dict


class CH:
    def __init__(
        self,
        ch_credentials: Dict[str, Any],
        dag_folder_path: Union[str, os.PathLike[str]] | None = None,
        timeout: Tuple[float, float] = (3.0, 30.0),  # (connect, read)
    ):
        if dag_folder_path is None:
            dag_folder_path = Path(__file__).resolve().parent / "sql"
        dag_folder_path = str(dag_folder_path)

        self.dag_folder_path = dag_folder_path
        self.timeout = timeout

        self.ch_url = f"http{'s' if ch_credentials['ssl'] else ''}://{ch_credentials['host']}:{ch_credentials['port']}"
        self.ch_credentials = (ch_credentials["user"], ch_credentials["password"])
        self.queries = get_dag_queries(dag_folder_path)

        # Reuse HTTP connections.
        self.http = requests.Session()
        self.http.auth = self.ch_credentials

    def _resolve_query(self, query: str, **kwargs) -> str:
        template = self.queries.get(query)
        if template is None:
            available = ", ".join(sorted(self.queries.keys()))
            raise KeyError(
                f"Unknown query name: {query!r}. "
                f"Expected a .sql file name from {self.dag_folder_path} (without extension). "
                f"Available: [{available}]"
            )
        return template.format(**kwargs)

    def _send_to_ch(self, **kwargs) -> requests.Response:
        r = self.http.post(
            f"{self.ch_url}",
            timeout=self.timeout,
            proxies={"http": None, "https": None},
            **kwargs,
        )
        if not r.ok:
            raise ClickHouseError(f"ClickHouse error: HTTP {r.status_code}: {r.text[:2000]}")
        return r

    def run_query(self, query: str, **kwargs) -> requests.Response:
        sql = self._resolve_query(query, **kwargs)
        return self._send_to_ch(data=sql.encode("utf-8"))

    def fetch_data(self, query: str, **kwargs) -> List[Dict[str, Any]]:
        """Fetch results as list[dict] using JSONEachRow."""
        sql = self._resolve_query(query, **kwargs)

        sql_with_format = f"{sql}\nFORMAT JSONEachRow"
        response = self._send_to_ch(data=sql_with_format.encode("utf-8"))

        text = response.text
        if not text.strip():
            return []

        rows: List[Dict[str, Any]] = []
        for line in text.splitlines():
            if not line:
                continue
            rows.append(json.loads(line))
        return rows

    def insert_data(self, table_name: str, data: Iterable[Dict[str, Any]], db_name: str = "umbra"):
        rows = list(data)
        if not rows:
            return

        values = "\n".join([json.dumps(row, default=str, ensure_ascii=False) for row in rows]) + "\n"
        params = {
            "query": (
                f"INSERT INTO {db_name}.{table_name} "
                f"SETTINGS max_partitions_per_insert_block = 100000, "
                f"insert_deduplicate = 0 "
                f"FORMAT JSONEachRow"
            )
        }
        self._send_to_ch(data=values.encode("utf-8"), params=params)
