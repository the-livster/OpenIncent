# Licensing

OpenIncent is available under two licenses. Choose the one that fits your use case.

## Default: AGPL-3.0-only

The engine (`icm-engine`) is free and open source under the [GNU Affero General Public License v3.0](https://www.gnu.org/licenses/agpl-3.0.html) (AGPL-3.0-only).

This means you can:

- Use it for any purpose.
- Read and audit every line of source code.
- Modify it and run your modified version.
- Distribute copies (modified or unmodified).

If you offer OpenIncent as a network service (e.g., a hosted SaaS that other people access over a network), the AGPL requires you to make your modified source code available to those users. This is the key obligation beyond the standard GPL — it closes the "application service provider" loophole.

Running the engine on your own infrastructure for your own internal use (processing your own commissions) does **not** trigger the network-service clause, because you are not offering it to others.

## Commercial license

If you want to embed OpenIncent in a proprietary product, or use it without the AGPL's obligations (including the network-service source-sharing requirement), a commercial license is available.

You probably need a commercial license if:

- You are building a commercial SaaS that includes the engine and you do not want to release your modifications under the AGPL.
- You are embedding the engine in a proprietary on-premise product.
- Your legal or compliance team requires a non-copyleft license.

The commercial license gives you a perpetual, non-exclusive right to use, modify, and distribute the engine under terms that do not require you to open-source your own code.

## How to obtain a commercial license

Contact the maintainer at **[hello@openincent.com](mailto:hello@openincent.com)** with:

- Your name and organization.
- A brief description of your intended use (product, internal tool, etc.).
- The scope (number of users, deployment model).

Pricing and terms are discussed individually.

## FAQ

**Can I use OpenIncent for free internally?**  
Yes. The AGPL allows internal use, modification, and redistribution. The network-service clause only matters if you expose it to other organizations or users over a network.

**What if I contribute code?**  
Contributions are licensed under the AGPL by default. To keep the project dual-licensable, contributors sign a CLA (see [`CONTRIBUTING.md`](./CONTRIBUTING.md)) granting the maintainer the right to also offer their contribution under a commercial license.

**Does the commercial license cover the whole project?**  
Yes — a commercial license covers `icm-engine`. The desktop UI (`icm-ui`) and other components may have their own licensing. Contact the maintainer to confirm scope.

---

*This document is a plain-language summary, not legal advice. The full terms are in the [`LICENSE`](./LICENSE) file and any commercial license agreement you sign. If you need legal certainty around your obligations or rights, consult a lawyer.*
