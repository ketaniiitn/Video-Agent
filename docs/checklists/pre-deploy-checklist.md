# Pre-Deploy Checklist

- [ ] Migrations follow expand/contract and are applied before this deploy, not bundled
      with app code that already expects the contracted schema
- [ ] New behaviour is behind a feature flag
- [ ] Any prompt/model change is staged at 10% traffic, not shipped at 100% directly
- [ ] CI gates green: eval regression ≤ 3%, cost regression ≤ 20%
- [ ] Rollback plan noted (flag off / previous prompt version / previous migration step)
- [ ] `trace_id` and error-code plumbing verified on any new error path
