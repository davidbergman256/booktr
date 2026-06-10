// BookTr — šablona knihy (preambule; obsah se připojuje programově)
#set page(paper: "{{PAPER}}", margin: (x: 2.2cm, y: 2.4cm), numbering: "1")
#set text(lang: "cs", size: 11.5pt, hyphenate: true)
#set par(justify: true, first-line-indent: 1.2em, spacing: 0.65em, leading: 0.62em)
#set smartquote(enabled: true)

// Kapitoly: nová stránka, titulek na střed, odsazení
#show heading.where(level: 1): it => {
  pagebreak(weak: true)
  v(5em)
  align(center, text(size: 17pt, weight: "bold", it.body))
  v(2.5em)
}
