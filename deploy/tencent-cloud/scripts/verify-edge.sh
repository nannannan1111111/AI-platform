#!/usr/bin/env bash
set -Eeuo pipefail

site_domain="${1:?usage: verify-edge.sh <site-domain> [expected-ip]}"
expected_ip="${2:-}"

resolved_ip="$(getent ahostsv4 "$site_domain" | awk 'NR == 1 { print $1 }')"
[[ -n "$resolved_ip" ]] || {
  echo "DNS did not return an IPv4 address for $site_domain" >&2
  exit 1
}
if [[ -n "$expected_ip" && "$resolved_ip" != "$expected_ip" ]]; then
  echo "DNS resolved to $resolved_ip instead of $expected_ip" >&2
  exit 1
fi

redirect_headers="$(curl --silent --show-error --dump-header - --output /dev/null --max-time 10 "http://$site_domain/readyz")"
grep -Eq '^HTTP/[0-9.]+ 30[1278]' <<< "$redirect_headers" || {
  echo "HTTP did not redirect to HTTPS" >&2
  exit 1
}
grep -Eiq "^location: https://$site_domain/" <<< "$redirect_headers" || {
  echo "HTTP redirect target was not the expected HTTPS hostname" >&2
  exit 1
}

https_headers="$(curl --fail --silent --show-error --dump-header - --output /dev/null --max-time 10 "https://$site_domain/readyz")"
grep -Eiq '^strict-transport-security: max-age=31536000; includeSubDomains$' <<< "$https_headers" || {
  echo "HTTPS response did not contain the production HSTS policy" >&2
  exit 1
}
grep -Eiq "^content-security-policy: .*script-src 'self'.*script-src-attr 'none'" <<< "$https_headers" || {
  echo "HTTPS response did not contain the enforced CSP" >&2
  exit 1
}

unknown_host_status="$(curl --silent --output /dev/null --write-out '%{http_code}' --resolve "invalid.$site_domain:443:$resolved_ip" "https://invalid.$site_domain/readyz" || true)"
if [[ "$unknown_host_status" == "200" ]]; then
  echo "an unknown Host unexpectedly reached a successful application response" >&2
  exit 1
fi

echo "DNS, HTTP redirect, TLS readiness, HSTS, CSP, and unknown Host rejection passed for $site_domain"
