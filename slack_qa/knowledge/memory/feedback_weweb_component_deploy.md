---
name: WeWeb component deployment pattern
description: How to build and deploy WeWeb coded components from the tulletogether monorepo
type: feedback
---

Each WeWeb component needs its OWN GitHub repository — WeWeb only supports org/repo/branch in its import UI, no subfolder paths.

Deployment workflow for every new component:
1. Build the component locally: `node_modules/.bin/weweb build name=<component-name> type=wwobject` (run from inside the component folder)
2. Ensure `dist/manager.js` is NOT in .gitignore — commit it to the component folder in tulletogether
3. Create a new GitHub repo: `marthv/ww-<component-name>` (empty, no README)
4. Create a branch in tulletogether: `git checkout -b ww/<component-name>`
5. Copy component files to repo root: `cp -r components/<component-name>/. .`
6. Stage and commit: `git add ww-config.js src/wwElement.vue dist/manager.js package.json package-lock.json`
7. Add remote and push: `git remote add ww-<component-name> https://github.com/marthv/ww-<component-name>.git && git push ww-<component-name> ww/<component-name>:main`
8. Switch back to main: `git checkout main`
9. In WeWeb: + Import element → marthv → ww-<component-name> → main → set version

**Why:** WeWeb's import dialog only shows org/repo/branch — no subfolder support. Each component must be at the root of its own repo.
