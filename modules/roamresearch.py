#!/usr/bin/env python3
from itertools import chain
from typing import Iterable

from orger import StaticView
from orger.inorganic import node, link, OrgNode
from orger.common import dt_heading

import my.roamresearch as roamresearch


from subprocess import run, PIPE

def md2org(text: str) -> str:
    # TODO use batch?? or talk to a process
    r = run(
        ['pandoc', '-f', 'markdown', '-t', 'org', '--wrap=none'],
        check=True,
        input=text.encode('utf8'),
        stdout=PIPE,
    )
    return r.stdout.decode('utf8')


# todo ^^ ^^ things are highlight?
def roam_text_to_org(text: str) -> str:
    """
    Cleans up Roam artifacts and adapts for better Org rendering
    """
    for f, t in [
            ('{{[[slider]]}}', ''),
    ]:
        text = text.replace(f, t)
    org = md2org(text)
    org = org.replace(r'\_', '_') # unescape, it's a bit aggressive..
    return org


def roam_note_to_org(node: roamresearch.Node, top=False) -> Iterable[OrgNode]:
    """
    Converts Roam node into Org-mode representation
    """
    children = node.children
    empty = len(node.title or '') == 0 and len(node.body or '') == 0 and len(children) == 0
    if empty:
        # sometimes nodes are empty. Maybe accidentally pressed Enter?
        # just don't do anything in this case
        return

    title = node.title
    # org-mode target allows jumping straight into
    # conveniently, links in Roam are already represented as [[link]] !
    target = '' if title is None else f'<<{title}>> '
    heading = target + link(title='x', url=node.permalink)

    todo = None
    body = node.body
    if body is not None:
        for t in ('TODO', 'DONE'):
            ts = '{{[[' + t + ']]}}'
            if body.startswith(ts):
                todo = t
                body = body[len(ts):]

        body = roam_text_to_org(body)

        lines = body.splitlines(keepends=True)
        # display first link of the body as the heading
        if len(lines) > 0:
            heading = heading + ' ' + lines[0]
            body = ''.join(lines[1:])
            if len(body) == 0:
                body = None

    if top:
        heading = dt_heading(node.created, heading)

    yield OrgNode(
        todo=todo,
        heading=heading,
        body=body,
        children=list(chain.from_iterable(map(roam_note_to_org, children))),
    )


class RoamView(StaticView):
    def get_items(self):
        rr = roamresearch.roam()
        from concurrent.futures import ThreadPoolExecutor
        # todo might be an overkill, only using because of pandoc..
        with ThreadPoolExecutor() as pool:
            items = list(chain.from_iterable(pool.map(roam_note_to_org, rr.nodes)))

        yield from items


if __name__ == '__main__':
    RoamView.main()
