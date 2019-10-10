#!/usr/bin/env python3
from orger import StaticView
from orger.inorganic import node, link
from orger.common import dt_heading

import my.pinboard as pinboard

class PinboardView(StaticView):
    def get_items(self):
        def make_item(b: pinboard.Bookmark):
            return node(
                heading=dt_heading(b.created, link(title=b.title, url=b.url)),
                body=b.description,
                tags=b.tags,
            )
        return [make_item(b) for b in pinboard.get_bookmarks()]


test = PinboardView.make_test(
    heading='Cartesian Closed Comic #21',
    contains='doctorwho', # TODO predicate?
)

if __name__ == '__main__':
    PinboardView.main()

