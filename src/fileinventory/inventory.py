import os
import subprocess
import sqlite3
import hashlib
from io import StringIO
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Tuple, Optional, Union


@dataclass
class Options:
    '''
    `ignore_hidden` -> `bool`
        Ignore hidden files/folders.
    `ignore_system` -> `bool`
        Ignore system files/folders.
    `recursive` -> `bool`
        Also go through sub folders.
    `cutoff_size` -> `int`
        Cutoff size in bytes for files/folders to be included.
    `top_n` -> `int`
        Top N biggest files/folders arg.
    `target_dir` -> `pathlib.Path `
        Folder to traverse and inventory.  
    `db_path` -> `pathlib.Path`
        Path to sqlite db.
    '''   
    ignore_hidden: bool = field(default_factory=bool)
    ignore_system: bool = field(default_factory=bool)
    recursive: bool = field(default_factory=bool)
    cutoff_size: int = field(default_factory=int)
    top_n: int = field(default_factory=int)
    target_dir: Path = field(default_factory=Path)
    db_path: Path = field(default_factory=Path)

class FileInventory():
    '''
    Traverses a windows file path and stores information 
    regarding size and file count in a sqlite.db.
    
    `options` -> `FileInventory.Options`
        Class options. Default options explore source code parent folder.
    '''
    def __init__(self, options: Optional[Options] = None):
        self.options = options if options else Options(
            ignore_hidden=True,
            ignore_system=True,
            recursive=False,
            cutoff_size=0,
            top_n=1,
            target_dir=Path(__file__).resolve().parent,
            db_path=Path(__file__).resolve().parent.parent / "db" / "default.db",
        )
        self._process()

    #----PUBLIC----#
    def query(
        self,
        parent: Optional[Path] = None,
        *,
        min_size: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        '''
        Return largest entries first; parent filters immediate children.

        `parent` -> `pathlib.Path`
            Optinal, query direct children of parent.
        `min_size` -> `int`
            Optional, overides `cutoff_size`.
        `limit` -> `int`
            Optional, overides `top_n`.
        '''
        min_size = self.options.cutoff_size if min_size is None else min_size
        limit = self.options.top_n if limit is None else limit
        if min_size < 0 or limit < 0:
            raise ValueError("min_size and limit must be nonnegative")

        sql = "SELECT id, parent_id, path, size_bytes, item_count FROM entries"
        sql += " WHERE size_bytes >= ?"
        parameters = [min_size]
        if parent is not None:
            sql += " AND parent_id = ?"
            parameters.append(self._path_id(Path(parent)))
        sql += " ORDER BY size_bytes DESC, path ASC"
        if limit:
            sql += " LIMIT ?"
            parameters.append(limit)

        db_uri = self.options.db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(db_uri, uri=True)
        try:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()

    def view(
            self, 
            parent: Optional[Path] = None, 
            *, 
            min_size: Optional[int] = None, 
            limit: Optional[int] = None,
        ) -> None:
        '''
        Queries and prints to table.

        `parent` -> `pathlib.Path`
            Optinal, query direct children of parent.
        `min_size` -> `int`
            Optional, overides `cutoff_size`.
        `limit` -> `int`
            Optional, overides `top_n`.
        '''
        rows = self.query(parent, min_size=min_size, limit=limit)
        if not rows:
            print("No matching entries.")
            return
        print(f"{'Size (bytes)':>16}  {'Items':>10}  Path")
        print("-" * 70)
        for row in rows:
            print(f"{row['size_bytes']:>16,}  {row['item_count']:>10,}  {row['path']}")

    #----PRIVATE----#
    @staticmethod
    def _parse_cmd_line(line: str) -> Tuple[int, any]:
        line = [x for x in line.strip().split(' ') if x != '']
        if len(line) < 1:
            return(-1, '')
        if line[0] == 'Directory':
            return(0, ''.join(line[2:]))
        if line[1] == 'File(s)':
            return(1, (line[0], line[2].replace(',','')))
        return(-1, '')

    @staticmethod
    def _path_id(path: Path) -> str:
        normalized = os.path.normcase(os.path.abspath(path))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    
    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id         TEXT PRIMARY KEY,
                parent_id  TEXT,
                path       TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                item_count INTEGER NOT NULL
            )
        """)
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_parent
            ON entries(parent_id)
        """)
    
    def _process(self) -> None:
        option = '/a'
        if self.options.ignore_hidden:
            option += '-h'
        if self.options.ignore_system:
            option += '-s'
        cmd = ['cmd','/c','dir',option,'/s'] if self.options.recursive \
            else ['cmd','/c','dir',option]
        result = StringIO(subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=self.options.target_dir,
            check=True
        ).stdout)
        db_path = self.options.db_path.resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        try:
            with connection:
                self._create_schema(connection)
                self._parse_cmd(result, connection)
        finally:
            connection.close()
        print(f"Database saved to: {db_path}")
    
    def _input_to_table(
        self,
        record: Dict[str, Union[str, int]],
        connection: sqlite3.Connection,
    ) -> None:
        path = Path(str(record["path"])).absolute()
        entry_id = self._path_id(path)
        parent_id = self._path_id(path.parent) if path.parent != path else None

        connection.execute("""
            INSERT INTO entries (
                id, parent_id, path, size_bytes, item_count
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                parent_id  = excluded.parent_id,
                path       = excluded.path,
                size_bytes = excluded.size_bytes,
                item_count = excluded.item_count
        """, (
            entry_id,
            parent_id,
            str(path),
            int(record['size']),
            int(record['items']),
        ))
    
    def _parse_cmd(self, result: StringIO, connection: sqlite3.Connection) -> None:
        path = Path('')
        for line in result:
            _type, text = self._parse_cmd_line(line)
            if _type == -1:
                continue
            if _type == 0:
                path = Path(text)
                continue
            if _type == 1:
                record = {
                    'path': str(path),
                    'items': int(text[0]),
                    'size': int(text[1])                    
                }
                self._input_to_table(record, connection)