// BookTr — šablona knihy (preambule; obsah se připojuje programově)
#set page(paper: "{{PAPER}}", margin: (x: 2.0cm, y: 2.2cm), numbering: "1")
#set text(lang: "cs", size: {{FONT_SIZE}}pt, hyphenate: true)
#set par(justify: true, first-line-indent: 1.2em, spacing: 0.65em, leading: 0.58em)
#set smartquote(enabled: true)
#set footnote.entry(clearance: 0.8em, gap: 0.4em, indent: 1em)
#show footnote.entry: set text(size: calc.max(11pt, {{FONT_SIZE}}pt * 0.8))

// Kapitoly: nová stránka, titulek na střed, odsazení
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  v(2em)
  align(center, text(size: 1.35em, weight: "bold", it.body))
  v(1.5em)
}
