def coluna_rodovia_snv(colunas) -> str | None:
    return next((c for c in colunas
                 if any(k in c.lower() for k in
                    ["sigla", "ds_sigla", "nome_rod", "nm_rod", "rodovia",
                     "codigo", "cd_rod", "br_", "snv_", "nome"])),
                None)


def rodovias_snv_processadas(snv_gdf) -> list[str]:
    sig_col = coluna_rodovia_snv([c for c in snv_gdf.columns if c != "geometry"])
    if not sig_col:
        return []
    valores = snv_gdf[sig_col].dropna().astype(str)
    return sorted(v for v in valores.unique().tolist() if v.strip())
