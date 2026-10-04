import { withAui } from "@assistant-ui/next";
import type { NextConfig } from "next";

// no Next.js badge in the corner while developing (`next dev`)
const nextConfig: NextConfig = { devIndicators: false };

export default withAui(nextConfig);
