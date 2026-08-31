"""What an attachment's bytes mean.

Dispatch is on magic bytes, never on the extension: CMS-era attachments are
routinely mislabeled, and several of this corpus's ".pdf" urls are an HTML
soft-404.

PDFs and Word files take deliberately different routes. A PDF goes through
poppler's bbox output - a tree of page/block/line/word coordinates with no
composed text at all - and the structure is derived from geometry, which is
what recovers headings, lists and spec tables. A .doc keeps text only, because
the DocBook route was built, calibrated over every cached Word file, and
dropped: it flattened nested lists and lost paragraph breaks to gain one bold
headline.

A document that converts to nothing is reported, never quietly given the other
route. A silent fallback would turn a converter failure into a row that merely
looks worse, and nobody would learn which document broke it.
"""
