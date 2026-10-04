"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type FeaturesMap = Record<string, boolean>;

/**
 * Feature-flag helper. Reads the operator-controlled flag map from
 * `GET /api/v1/features` once per minute and exposes a stable lookup. Flagged
 * surfaces render nothing (or a "turned off" message) while disabled so each
 * capability can be killed independently without a deploy.
 */
export function useFeatures(): { features: FeaturesMap; isEnabled: (name: string) => boolean } {
  const query = useQuery({
    queryKey: ["feature-flags"],
    queryFn: () => api<{ features: FeaturesMap }>("/api/v1/features"),
    staleTime: 60_000,
    retry: false,
  });
  const features = query.data?.features ?? {};
  return {
    features,
    isEnabled: (name: string) => Boolean(features[name]),
  };
}

export function useIsFeatureEnabled(name: string): boolean {
  return useFeatures().isEnabled(name);
}