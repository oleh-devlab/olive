# About quickly reloading cogs

On Discord, this is done using commands like `/reload_cogs`, but there are a few things to keep in mind:
- If the cog you’re reloading imports modules from `core` or `modules`, and the code in `core` or `modules` has been changed, the reloaded cog won’t receive the new changes from those modules. You will need to reload the entire bot. [^1]

> In other words, **changes to the `core` and `modules` are not pulled in** by reloading cogs.

[^1]: We haven’t yet written a system to fix this, as it is overkill for the current scale of the project.