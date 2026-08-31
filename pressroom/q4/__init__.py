"""The Q4 Inc. investor-relations platform, as two firms happen to run it.

A platform, not a firm: Intel and AMD publish through the same product, so the
listing shape, the pagination probe and the article container are one parser
with two configurations. Both are live sites, so their pages come through the
politeness path and a reparse costs no request.
"""
