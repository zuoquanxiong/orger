#!/usr/bin/env python3
import argparse
from argparse import ArgumentParser, Namespace
from collections import Counter
import inspect
import json
from pathlib import Path
from subprocess import check_call
import sys
from typing import Any, List, Tuple, Iterable, Optional, Union, Callable, Dict

from .inorganic import OrgNode, TimestampStyle
from .state import JsonState
from .atomic_append import atomic_append_check, assert_not_edited
from .common import orger_user_dir
from .logging_helper import make_logger


# TODO tests for determinism? not sure where should they be...
# think of some generic thing to test that?

Key = str
OrgWithKey = Tuple[Key, OrgNode]


_style_map: Dict[str, TimestampStyle] = {
    k.lower(): v  # type: ignore[misc]
    for k, v in TimestampStyle._member_map_.items()
}

class OrgView:
    logger_tag: Optional[str] = None
    DEFAULT_HEADER: str = '# should be overridden'

    # TODO cmdline args shouldn't be none?
    def __init__(
            self,
            cmdline_args: Optional[Namespace]=None,
            file_header: Optional[str]=None,
    ) -> None:
        self.cmdline_args: Namespace = cmdline_args if cmdline_args is not None else Namespace()
        tag = self.name() if self.logger_tag is None else self.logger_tag
        self.logger = make_logger(tag, level='debug')

        tool = Path(inspect.getfile(self.__class__)).absolute()
        self.file_header = file_header if file_header is not None else self.DEFAULT_HEADER.format(tool=tool)

    @property
    def args(self) -> Namespace:
        # TODO deprecate cmdline_args?
        return self.cmdline_args

    @classmethod
    def name(cls):
        return cls.__name__

    def get_items(self) -> Iterable:
        raise NotImplementedError

    def main_common(self) -> None:
        timestamp_style = self.args.timestamps
        from .common import settings
        # hacky, but does the trick for now...
        settings.DEFAULT_TIMESTAMP_STYLE = _style_map[timestamp_style]

        pandoc = self.args.pandoc
        settings.USE_PANDOC = pandoc

    @classmethod
    def parser(cls) -> ArgumentParser:
        F = lambda prog: argparse.ArgumentDefaultsHelpFormatter(prog, width=120)
        p = argparse.ArgumentParser(formatter_class=F)

        p.add_argument(
            '--disable-pandoc',
            action='store_false',
            dest='pandoc',
            help='Pass to disable pandoc conversions to org-mode (it might be slow in some cases)',
        )
        p.add_argument(
            '--timestamps',
            type=str,
            choices=list(_style_map.keys()),
            default='inactive',
            help="timestamp style, default '%(default)s'",
        )
        return p


# TODO wonder if I could reuse append bits here?
class Mirror(OrgView):
    """
    *Mirror* (old name =StaticView=): mirrors *all data* from a source, and generated from scratch every time, hence *read only*.
    """

    DEFAULT_HEADER = '''
# This file is AUTOGENERATED by {tool}
# It's deliberately read-only, because it will be overwritten next time Orger is run.
# If you want to edit it anyway, you can use chmod +w in your terminal, or M-x toggle-read-only in Emacs.
'''.lstrip()

    # allowed to be either for Mirror
    Results = Iterable[Union[OrgNode, OrgWithKey]]

    @classmethod
    def main(cls, setup_parser=None) -> None:
        p = cls.parser()
        og = p.add_mutually_exclusive_group()
        og.add_argument('--to', type=Path, default=Path(cls.name() + '.org'), help='Filename to output')
        og.add_argument('--stdout', action='store_true', help='pass to print output to stdout, useful for testing/debugging')
        if setup_parser is not None:
            setup_parser(p)

        args = p.parse_args()
        inst = cls(cmdline_args=args)
        inst.main_common()
        inst._run(to=args.to, stdout=args.stdout)

    def get_items(self) -> Iterable:
        raise NotImplementedError

    def _run(self, to: Path, stdout: bool) -> None:
        org_tree = self.make_tree()
        rtree = org_tree.render(level=0)

        if stdout:
            print(rtree)
            return

        # otherwise output to file
        assert_not_edited(to)
        # again, not properly atomic, but hopefully enough
        # TODO create a github issue, maybe someone comes up with proper way of solving this
        to.touch()
        check_call(['chmod', '+w', to])
        to.write_text(rtree)
        check_call(['chmod', '-w', to])


    def make_tree(self) -> OrgNode:
        items: List[OrgNode] = []
        for p in self.get_items():
            # it's ok to use items without keys in View
            if isinstance(p, OrgNode):
                items.append(p)
            else:
                items.append(p[1]) # presumably, OrgWithKey

        split = self.file_header.splitlines(keepends=True)
        heading = split[0].rstrip()
        body = ''.join(split[1:])
        return OrgNode(
            # TODO shit. are newlines sanitized from file header??
            heading=heading,
            body=body,
            children=items,
            escaped=True,
        )

    @classmethod
    def make_test(cls, *, heading: str, contains: Optional[str]=None) -> Callable[[], None]:
        from .inorganic import _from_lazy
        def pick_heading(root: OrgNode, text: str) -> Optional[OrgNode]:
            if text in _from_lazy(root.heading):
                return root
            for ch in root.children:
                chr = pick_heading(ch, text)
                if chr is not None:
                    return chr
            else:
                return None

        def test():
            tree = cls().make_tree() # TODO make sure it works on both static and interactive?
            ll = pick_heading(tree, heading)
            assert ll is not None
            if contains is not None:
                assert contains in ll.render()
        return test
StaticView = Mirror


class Queue(OrgView):
    """
    *Queue* (old name =InteractiveView=): works as a queue, *only previously unseen items* from the data source are added to the output org-mode file.

    To keep track of previously seen iteems, it's using a separate JSON =state= file.

    A typical usecase is a todo list, or a content processing queue.
    You can use such a module as you use any other org-mode file: schedule/refile/comment/set priorities, etc.
    """

    DEFAULT_HEADER = '''
# This file is AUTOGENERATED by {tool}
'''.lstrip()

    Results = Iterable[OrgWithKey]

    def _run(
            self,
            to: Path,
            stdout: bool,
            state_path: Path,
            init: bool=False,
            dry_run: bool=False,
    ) -> None:
        self.logger.info('Using state file %s', state_path)

        appender: Callable[[str], Any]
        if stdout:
            appender = lambda s: sys.stdout.write(s)
        else:
            appender = lambda s: atomic_append_check(to, s)

            if not to.exists() and not init:
                err = RuntimeError(f"{to} doesn't exist! Try running with --init")
                if sys.stdin.isatty():
                    resp = input(f"{to} doesn't exist. Create empty file? y/n ").strip().lower()
                    if resp != 'y':
                        raise err
                else:
                    raise err

        state = JsonState(
            path=state_path,
            logger=self.logger,
            dry_run=dry_run,
        )
        items = list(self.get_items())

        dups = [k for k, cnt in Counter(i[0] for i in items).items() if cnt > 1]
        if len(dups) > 0:
            raise RuntimeError(f'Duplicate items {dups}')

        if not to.exists():
            self.logger.warning("target %s didn't exist, initializing!", to)
            appender(self.file_header + '\n')

        for key, item in items:
            def action(item=item):
                # not sure about this newline, but better to have extra whitespace than rely on trailing
                rendered = '\n' + item.render(level=1)
                appender(rendered)
            self.logger.debug('processing %s', key)
            state.feed(
                key=key,
                value=item,  # TODO not sure about this one... perhaps only link?
                action=action,
            )

    def get_items(self) -> Iterable[OrgWithKey]:
        raise NotImplementedError

    @classmethod
    def main(cls, setup_parser=None) -> None:
        default_state = orger_user_dir() / 'states' / (cls.name() + '.state.json')
        p = cls.parser()
        og = p.add_mutually_exclusive_group()
        og.add_argument('--to'   , type=Path, default=Path(cls.name() + '.org')       , help='file where new items are added')
        og.add_argument('--stdout', action='store_true', help='pass to print output to stdout, useful for testing/debugging')
        p.add_argument('--state', type=Path, default=default_state, help='state file for keeping track of handled items')
        p.add_argument('--init', action='store_true') # todo not sure if I really need it?
        p.add_argument('--dry-run', action='store_true', help='Run without modifying the state file')
        if setup_parser is not None:
            setup_parser(p)

        args = p.parse_args()
        inst = cls(cmdline_args=args)
        inst.main_common()
        inst._run(
            to=args.to,
            stdout=args.stdout,
            state_path=args.state,
            init=args.init,
            dry_run=args.dry_run,
        )


def test_org_view_overwrite(tmp_path: Path):
    class TestView(StaticView):
        def __init__(self, items: List[OrgWithKey], *args, **kwargs) -> None:
            super().__init__(*args, file_header='# autogenerated!\n#+TITLE: sometitle\nalso text\n', **kwargs) # type: ignore
            self.items = items

        def get_items(self):
            return self.items

    rpath = tmp_path / 'test.org'

    TestView([])._run(to=rpath, stdout=False)
    assert rpath.read_text() == '''
# autogenerated!
#+TITLE: sometitle
also text
'''.lstrip()

    TestView([
        # TODO shit, it's gonna use implicit date??
        ('first' , OrgNode(heading='whatever')),
        ('second', OrgNode(heading='alala')), # TODO why was that even necessary??
    ])._run(to=rpath, stdout=False)
    # TODO eh, perhaps use trailing space?
    assert rpath.read_text() == """
# autogenerated!
#+TITLE: sometitle
also text

* whatever
* alala""".lstrip()


def test_org_view_append(tmp_path: Path) -> None:
    class TestView(Queue):
        def __init__(self, items: List[OrgWithKey], *args, **kwargs) -> None:
            super().__init__(*args, file_header='# autogenerated!', **kwargs) # type: ignore
            self.items = items

        def get_items(self):
            for i in self.items:
                yield i

    rpath = tmp_path / 'res.org'
    spath = tmp_path / 'state.json'

    def run_view(items, **kwargs):
        TestView(items)._run(
            to=rpath,
            stdout=False,
            state_path=spath,
            **kwargs,
        )

    def get_state():
        return set(json.loads(spath.read_text()).keys())

    items = []
    run_view([], init=True)
    assert rpath.read_text() == """
# autogenerated!
""".lstrip()
    # TODO do we need to touch state too??

    items.append(
        ('first', OrgNode(heading='i am first')),
    )
    run_view(items)
    assert rpath.read_text() == """
# autogenerated!

* i am first""".lstrip()
    assert get_state() == {'first'}


    items.append(
        ('second', OrgNode('i am second')),
    )
    run_view(items)
    assert rpath.read_text() == """
# autogenerated!

* i am first
* i am second""".lstrip()
    assert get_state() == {'first', 'second'}


    rpath_time = rpath.stat().st_mtime
    spath_time = spath.stat().st_mtime
    run_view(items)
    assert rpath.stat().st_mtime == rpath_time
    assert spath.stat().st_mtime == spath_time

