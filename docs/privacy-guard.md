# Privacy guard

Kitet stoppar privata uppgifter från att hamna i ett publikt repo. Det utökar gitleaks standardregler med egna regler för konsumentmejladresser, privata IPv4-adresser, Tailscale-värdnamn och hemkatalogssökvägar.

## Filer

| Fil | Roll |
|-----|------|
| `.gitleaks.toml` | Regler. `[extend] useDefault = true` plus allowlists. |
| `.pre-commit-config.yaml` | Officiella gitleaks-hooken, `rev: v8.30.1`, med `--config=.gitleaks.toml`. |
| `.github/workflows/privacy-guard.yml` | Samma config i CI, utan licensnyckel och utan hemligheter. |

## Lokalt

```bash
# en gång per klon
pip install pre-commit   # eller brew install pre-commit
pre-commit install

# samma kontroll som CI: trädet vid HEAD, inte äldre historik
gitleaks detect --no-git --source . --config .gitleaks.toml --redact --no-banner
```

Hooken kör `gitleaks git --pre-commit --redact --staged` och läser `.gitleaks.toml`. `pre-commit install` måste köras i varje klon; den följer inte med git.

## CI

Workflowen laddar ner den öppna gitleaks-binären (v8.30.1, MIT) och kör `gitleaks detect --no-git`. `gitleaks-action` används inte, eftersom den kräver `GITLEAKS_LICENSE` för organisationskonton. Inga Actions-secrets behövs.

`fetch-depth: 1` och `--no-git` gör att bara det utcheckade trädet skannas. Borttagna uppgifter i äldre commits fäller inte bygget. Checkout-katalogen `.git/` är allowlistad så att en tillfällig CI-credential inte räknas som filinnehåll.

## Vad reglerna släpper igenom

- `*@users.noreply.github.com`, adresser som börjar med `noreply@`, samt `example.com` och `example.org`
- nätverksadresser som slutar på `.0` och har en CIDR-längd, till exempel `10.0.0.0/8`, `192.168.0.0/16` och `100.64.0.0/10`
- RFC 5737-adresser, `127.0.0.1`, `0.0.0.0` och platshållare som `100.64.0.x`
- `example.ts.net`, `<host>.ts.net` och värdnamn som innehåller `tailnet`
- användarnamnen `<user>`, `user`, `runner`, `runneradmin`, `ubuntu`, `root`, `$USER` och `%USERPROFILE%`, samt sökvägar som börjar med `~`

Lockfiler (`package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Cargo.lock`, `poetry.lock` med flera) och katalogerna `vendor/`, `node_modules/` och `third_party/` skannas inte.

Skriv aldrig en riktig privat adress eller ett riktigt användarnamn i regler, tester eller dokumentation. Använd platshållarna ovan.
