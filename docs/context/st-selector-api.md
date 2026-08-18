# st selector api

> ST's product-selector grid API — the three level-id families, and how sub-family pages hide their own id.

ST's public product selector, used by `stproducts` (stm32-product-selector/):

```
https://www.st.com/bin/st/selectors/cxst/en.cxst-ps-grid.html/{levelId}.json
https://www.st.com/bin/st/selectors/cxst/en.cxst-rpn-info.html/{productId}.json
```

**Three** level-id families work against the grid, not two: `SS####` (series),
`SC####` (catalogue/class), and `LN####` (product line). `LN` is the
non-obvious one — it is the only way to reach a *sub-family* workbook such as
STM32F2x5 (`LN1433`), STM8AF52 (`LN1543`) or STM32MP131 (`LN2413`).

Sub-family pages embed their **parent's** SS id all over the markup, so
scraping `SS\d+` off `stm32f2x5.html` silently resolves it to the 38-row
STM32F2 series grid. The reliable source is
`window.productHierarchy = "LN1433-SS1575-SC2154-CL1734-FM141"`, which is
most-specific-first — the leading component is the page's own id. `CL` and
`FM` are hierarchy bookkeeping and return HTTP 400 from the grid.

Transport must be `curl_cffi` with `impersonate="chrome"` (Akamai TLS
fingerprinting), same as [stm-table-extractor-context](stm-table-extractor-context.md)'s stm32fetch.

Related: [st-export-rendering-rules](st-export-rendering-rules.md)
