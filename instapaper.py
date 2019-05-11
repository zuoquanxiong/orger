#!/usr/bin/env python3
from typing import Collection

from kython.org_tools import link as org_link

from org_view import OrgViewOverwrite, OrgWithKey
from org_utils import OrgTree, as_org, pick_heading

from my.instapaper import get_pages


class IpView(OrgViewOverwrite):
    file = __file__
    logger_tag = 'instapaper-view'

    # pylint: disable=unsubscriptable-object
    def get_items(self) -> Collection[OrgWithKey]:
        return [(
            page.bookmark.bid,
            OrgTree(
                as_org(
                    created=page.bookmark.dt,
                    heading=f'{org_link(title="ip", url=page.bookmark.instapaper_link)}   {org_link(title=page.bookmark.title, url=page.bookmark.url)}',
                ),
                [
                    OrgTree(as_org(
                        created=hl.dt,
                        heading=org_link(title="ip", url=page.bookmark.instapaper_link),
                        body=hl.text,
                    )) for hl in page.highlights
                ]
            )
        # TODO make sure as_org figures out the date
    # TODO autostrip could be an option for formatter
        ) for page in get_pages()]
        # TODO could put links in org mode links? so not as much stuff is displayed?
        # TODO reverse order? not sure...
        # TODO unique id meaning that instapaper manages the item?
        # TODO spacing top level items could be option of dumper as well?
        # TODO better error handling, cooperate with org_tools


# TODO hmm. wereyouhere could explore automatically, perhaps even via porg?
# make it a feature of renderer?
# although just need to make one space tabulation, that'd solve all my problems
def test():
    org_tree = IpView().make_tree()
    ll = pick_heading(org_tree, 'Life Extension Methods')
    assert ll is not None
    assert len(ll.children) > 4
    assert any('sleep a lot' in c.item for c in ll.children)


def main():
    IpView.main(default_to='instapaper.org')

if __name__ == '__main__':
    main()
