/**
 * Network classification for abuse limits: classifies requests as "hosting" or "other".
 *
 * Privacy decision (20 Nov 2026): no browser or device fingerprinting. Limits use network data
 * only, specifically request.cf.asn and request.cf.asOrganization provided by Cloudflare.
 * VPN and proxy exit nodes mostly reside on hosting and datacenter networks; grouping by ASN
 * prevents bypassing limits by rotating IP addresses within a single hosting provider.
 *
 * Sources:
 * 1. Bundled ASN list (HOSTING_ASNS):
 *    Compiled from public BGP routing databases (bgp.he.net), PeeringDB (peeringdb.com),
 *    and regional internet registry records (ARIN, RIPE, APNIC WHOIS). Covers major cloud
 *    providers (AWS, Google Cloud, Microsoft Azure, Cloudflare, Oracle Cloud, DigitalOcean,
 *    Hetzner, OVH, Linode/Akamai, Vultr, Alibaba, Tencent, Scaleway) and major datacenter /
 *    VPN infrastructure operators (M247, Datacamp, Leaseweb, Proton AG, Tefincom, Fastly,
 *    Cogent, Contabo, Equinix).
 * 2. Organization keyword list (HOSTING_KEYWORDS):
 *    Matches request.cf.asOrganization (case-insensitive substring match):
 *    "hosting", "cloud", "datacenter", "data center", "server", "vps", "vpn", "proxy".
 *    Source: Cloudflare Workers IncomingRequestCfProperties documentation.
 */

export type NetworkClass = "hosting" | "other";

export interface AsnEntry {
  readonly asn: number;
  readonly name: string;
  readonly source: string;
}

export const HOSTING_KEYWORDS: readonly string[] = [
  "hosting",
  "cloud",
  "datacenter",
  "data center",
  "server",
  "vps",
  "vpn",
  "proxy",
] as const;

export const HOSTING_ASNS: readonly AsnEntry[] = [
  // Amazon AWS
  { asn: 16509, name: "Amazon.com, Inc. (AWS)", source: "bgp.he.net/AS16509" },
  { asn: 14618, name: "Amazon.com, Inc. (AWS)", source: "bgp.he.net/AS14618" },
  // Google Cloud
  { asn: 15169, name: "Google LLC", source: "bgp.he.net/AS15169" },
  { asn: 396982, name: "Google Cloud", source: "peeringdb.com/net/14352" },
  // Microsoft Azure
  { asn: 8075, name: "Microsoft Corporation (Azure)", source: "bgp.he.net/AS8075" },
  // Cloudflare
  { asn: 13335, name: "Cloudflare, Inc.", source: "bgp.he.net/AS13335" },
  { asn: 209242, name: "Cloudflare London / Warp", source: "bgp.he.net/AS209242" },
  // DigitalOcean
  { asn: 14061, name: "DigitalOcean, LLC", source: "bgp.he.net/AS14061" },
  // Hetzner
  { asn: 24940, name: "Hetzner Online GmbH", source: "bgp.he.net/AS24940" },
  { asn: 213230, name: "Hetzner Online GmbH", source: "bgp.he.net/AS213230" },
  // OVH
  { asn: 16276, name: "OVH SAS", source: "bgp.he.net/AS16276" },
  // Linode / Akamai
  { asn: 63949, name: "Linode, LLC", source: "bgp.he.net/AS63949" },
  { asn: 20940, name: "Akamai Technologies, Inc.", source: "bgp.he.net/AS20940" },
  // Oracle Cloud
  { asn: 31898, name: "Oracle Corporation (OCI)", source: "bgp.he.net/AS31898" },
  // Vultr / Choopa
  { asn: 20473, name: "The Constant Company, LLC (Vultr)", source: "bgp.he.net/AS20473" },
  // VPN / Datacenter infrastructure operators
  { asn: 9009, name: "M247 Ltd (VPN host / transit)", source: "bgp.he.net/AS9009" },
  { asn: 60068, name: "Datacamp Limited (CDN77)", source: "bgp.he.net/AS60068" },
  { asn: 16265, name: "Leaseweb Netherlands B.V.", source: "bgp.he.net/AS16265" },
  { asn: 62371, name: "Proton AG (ProtonVPN)", source: "bgp.he.net/AS62371" },
  { asn: 212238, name: "Datacamp / Tefincom (NordVPN)", source: "bgp.he.net/AS212238" },
  { asn: 54113, name: "Fastly", source: "bgp.he.net/AS54113" },
  { asn: 174, name: "Cogent Communications", source: "bgp.he.net/AS174" },
  { asn: 51167, name: "Contabo GmbH", source: "bgp.he.net/AS51167" },
  { asn: 46562, name: "Colo@ (colocation)", source: "bgp.he.net/AS46562" },
  // Alibaba Cloud
  { asn: 45102, name: "Alibaba Cloud (US)", source: "bgp.he.net/AS45102" },
  { asn: 37963, name: "Alibaba Cloud (CN)", source: "bgp.he.net/AS37963" },
  // Tencent Cloud
  { asn: 132203, name: "Tencent Building, Kejizhongyi Avenue", source: "bgp.he.net/AS132203" },
  { asn: 132591, name: "Tencent Cloud International", source: "bgp.he.net/AS132591" },
  // Scaleway
  { asn: 12876, name: "Scaleway S.A.S.", source: "bgp.he.net/AS12876" },
];

export const HOSTING_ASN_SET: ReadonlySet<number> = new Set(HOSTING_ASNS.map((e) => e.asn));

/**
 * Classifies a network as "hosting" or "other".
 * - "hosting": ASN is in the bundled HOSTING_ASNS list, or asOrganization contains a keyword.
 * - "other": residential, mobile, or enterprise network not matching the above.
 */
export function classifyNetwork(
  asn: number | null | undefined,
  asOrganization: string | null | undefined,
): NetworkClass {
  if (typeof asn === "number" && Number.isInteger(asn) && HOSTING_ASN_SET.has(asn)) {
    return "hosting";
  }
  if (typeof asOrganization === "string" && asOrganization.trim().length > 0) {
    const lower = asOrganization.toLowerCase();
    for (const kw of HOSTING_KEYWORDS) {
      if (lower.includes(kw)) {
        return "hosting";
      }
    }
  }
  return "other";
}

/** Extracts network metadata and classification from incoming Request.cf. */
export function networkFromRequest(request: Request): {
  netClass: NetworkClass;
  asn: number | null;
  asOrganization: string | null;
} {
  const cf = (request as Request & { cf?: { asn?: number; asOrganization?: string } }).cf;
  const asn = typeof cf?.asn === "number" && Number.isInteger(cf.asn) ? cf.asn : null;
  const asOrganization = typeof cf?.asOrganization === "string" ? cf.asOrganization : null;
  const netClass = classifyNetwork(asn, asOrganization);
  return { netClass, asn, asOrganization };
}
