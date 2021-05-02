import sys

import ujson
import click


class SwapList:
    """Used to generate, store and update list for LP swap
    """
    def __init__(self, path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                self._path = path
                self._data = ujson.load(f)
                click.secho('Swap list file loaded', fg='blue')
        except FileNotFoundError:
            click.secho('Swap list file not found', fg='red')
            raise
        except ValueError:
            click.secho('Swap list file broken', fg='red')
            raise

    @classmethod
    def get_or_create(cls, path):
        try:
            sl = cls(path)
        except (FileNotFoundError, ValueError):
            with open(path, 'w', encoding='utf-8') as f:
                ujson.dump({}, f, ensure_ascii=False, indent=8)
            sl = cls(path)
        return sl

    def get(self, name):
        try:
            return self._data[name]
        except KeyError:
            click.secho(f'Failed to find an addres for {name}\n', fg='red')
            sys.exit(1)

    def add(self, name, address):
        self._data[name] = address
        with open(self._path, 'w', encoding='utf-8') as f:
            ujson.dump(self._data, f, ensure_ascii=False, indent=8)

    def to_list(self):
        return [{'name': name, 'address': address} for name, address in self._data.items()]
