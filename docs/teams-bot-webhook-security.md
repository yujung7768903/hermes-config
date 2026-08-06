# Teams Bot Webhook 보안 구성 정리

## 현재 EC2 환경

```
인터넷 -> Bastion -> EC2 (private IP만 있음, 퍼블릭 IP 없음)
                          |
                          NAT Gateway (아웃바운드 전용, IP: 3.39.2.0)
```

- EC2는 private 서브넷에 위치
- 외부에서 직접 인바운드 불가
- 아웃바운드는 NAT Gateway를 통해 가능
- `ifconfig.me` 조회 시 나오는 IP는 EC2가 아닌 NAT Gateway의 IP

---

## Webhook 노출 방법 비교

### 1. cloudflared 터널 (개발/테스트용)

**원리: 아웃바운드 연결로 양방향 통신**

```
1. EC2 ----아웃바운드 연결 먼저 open----> Cloudflare (대기)
2. Teams --------요청--------> Cloudflare
3. EC2 <------이미 열린 연결로 데이터 수신------
```

- TCP는 한번 연결되면 양방향 통신 가능
- 방화벽은 "EC2가 먼저 연결한 아웃바운드"로 인식 -> 허용
- SSH 터널, VPN도 같은 원리

**장점:**
- Security Group / Bastion 구조 변경 불필요
- 포트 오픈 불필요
- 설정 간단, 즉시 사용 가능

**단점/위험:**
- 트래픽이 Cloudflare 서버를 경유 (제3자 노출 가능)
- 개발용 도구, 프로덕션 비권장
- 터널 URL 유출 시 외부 접근 가능 (단, Bot Framework 서명 검증으로 가짜 메시지 주입은 어려움)

**실행:**
```bash
cloudflared tunnel --url http://localhost:3978
```

---

### 2. AWS ALB + ACM + WAF (프로덕션 권장)

**구조:**
```
인터넷 -> ALB (HTTPS/443) -> EC2:3978 (private 유지)
          Bastion -> EC2 (관리용, 그대로 유지)
```

**장점:**
- EC2는 여전히 private, 인터넷 직접 노출 없음
- AWS ACM 무료 SSL 인증서 자동 갱신
- 제3자 경유 없음 (트래픽이 AWS 내부에만 존재)
- WAF로 Microsoft Bot Framework IP만 허용 가능
- 프로덕션 수준 안정성

**구성 순서:**

1. ACM에서 도메인 인증서 발급 (무료)
   - Route53 또는 외부 DNS로 도메인 소유 인증

2. ALB 생성
   - Public Subnet에 배치
   - 리스너: HTTPS 443
   - 인증서: ACM 발급 인증서
   - 타겟 그룹: EC2:3978

3. Security Group 설정
   ```
   ALB SG : 인터넷 -> 443 허용
   EC2 SG : ALB SG -> 3978 허용 (ALB에서만 받도록 제한)
   ```

4. (선택) AWS WAF에서 Microsoft Bot Framework IP 대역만 허용
   - 그 외 모든 IP는 ALB에서 차단

5. Teams 봇 설정에 ALB 도메인 등록
   ```
   https://your-domain.com/api/messages
   ```

**단점:**
- ALB 비용 발생 (월 약 $15~20)
- 도메인 필요
- 설정이 cloudflared보다 복잡

---

## 결론

| 목적 | 방법 | 비고 |
|------|------|------|
| 개발 / 테스트 | cloudflared | 빠르게 시작 가능 |
| 프로덕션 | ALB + ACM + WAF | 가장 안전, 도메인 필요 |

---

## 참고: 169.254.169.254 란?

- **Instance Metadata Service (IMDS)** 전용 주소
- 클라우드 VM 내부에서만 접근 가능한 특수 IP
- AWS, GCP, Azure 등 거의 모든 클라우드가 동일 IP 사용
- 외부에서 절대 접근 불가 (라우팅 불가)

```
주요 엔드포인트:
  /latest/meta-data/public-ipv4     퍼블릭 IP
  /latest/meta-data/local-ipv4      프라이빗 IP
  /latest/meta-data/instance-id     인스턴스 ID
  /latest/meta-data/iam/security-credentials/  IAM 임시 토큰 (SSRF 공격 타깃)
```

**SSRF 위험:** 해커가 서버를 통해 이 주소를 조회하면 IAM 키 탈취 가능 -> 보안 스캐너가 CRITICAL로 경고하는 이유
