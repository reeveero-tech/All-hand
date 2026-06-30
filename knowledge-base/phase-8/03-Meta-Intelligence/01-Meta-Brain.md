# Meta Brain: Brain About The Brain

> **Who monitors the Core Brain?**

---

## Concept

```
Traditional Architecture (Phase 1-7):
┌─────────────────────────────────────────────────────────────┐
│                       Core Brain                               │
│  The ONE brain that makes all decisions                     │
└─────────────────────────────────────────────────────────────┘

New Architecture (Phase 8+):
┌─────────────────────────────────────────────────────────────┐
│                       Meta Brain                               │
│  Audits Core Brain decisions                               │
│  Does NOT issue commands                                  │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       Core Brain                               │
│  Makes all decisions (OPERATIONAL)                         │
└─────────────────────────────────────────────────────────────┘
```

---

## Meta Brain Responsibilities

```yaml
MetaBrain:
  # What Meta Brain DOES
  
  AUDIT:
    - Monitor Core Brain decision quality
    - Track decision patterns
    - Detect biases
    
  ANALYZE:
    - Analyze decision outcomes
    - Calculate success rates
    - Identify improvement areas
    
  SUGGEST:
    - Suggest policy improvements
    - Recommend ADR updates
    - Propose new capabilities
    
  DETECT:
    - Detect performance degradation
    - Detect repeated mistakes
    - Detect bias patterns
    
  REPORT:
    - Report to governance
    - Generate audit logs
    - Create improvement reports

  # What Meta Brain does NOT DO
  
  NOT_AUTHORIZED:
    - Issue execution commands
    - Select providers directly
    - Override Core Brain decisions
    - Make operational decisions
```

---

## Meta Brain Architecture

```yaml
MetaBrainArchitecture:
  # Meta Brain components
  
  components:
    DecisionAuditor:
      description: "Audits every Core Brain decision"
      functions:
        - Record decisions
        - Analyze decision quality
        - Detect decision patterns
        
    BiasDetector:
      description: "Detects biases in decisions"
      functions:
        - Track decision history
        - Identify bias patterns
        - Alert on concerning trends
        
    QualityMonitor:
      description: "Monitors decision quality"
      functions:
        - Calculate quality metrics
        - Track success rates
        - Detect degradation
        
    PolicyAdvisor:
      description: "Advises on policy improvements"
      functions:
        - Analyze policy effectiveness
        - Suggest policy changes
        - Recommend new ADRs
        
    PerformanceTracker:
      description: "Tracks Core Brain performance"
      functions:
        - Measure planning quality
        - Measure prediction accuracy
        - Measure outcome quality
```

---

## Decision Auditing

```yaml
DecisionAudit:
  # Every Core Brain decision is audited
  
  audit_record:
    decision_id: string
    timestamp: datetime
    
    decision:
      type: string          # STRATEGIC, TACTICAL, OPERATIONAL
      context: string        # What was being decided
      options_considered: list[string]
      selected: string
      reasoning: string
      
    outcome:
      status: enum          # SUCCESS, FAILURE, PARTIAL
      quality: decimal      # 0.0-1.0
      duration: duration
      cost: decimal
        
    analysis:
      bias_score: decimal  # 0.0-1.0 (higher = more biased)
      quality_score: decimal
      improvement_areas: list[string]
```

---

## Bias Detection

```yaml
BiasDetection:
  # Types of biases Meta Brain detects
  
  BIASES:
    provider_bias:
      description: "Always selecting same provider"
      detection:
        - Track provider selection history
        - Alert if >80% same provider
        
    complexity_bias:
      description: "Over-engineering solutions"
      detection:
        - Track solution complexity
        - Alert if complexity > threshold
        
    speed_bias:
      description: "Rushing decisions"
      detection:
        - Track decision time
        - Alert if decisions too fast for complexity
        
    cost_bias:
      description: "Always choosing cheapest"
      detection:
        - Track cost vs quality tradeoff
        - Alert if quality sacrificed for cost
        
    success_bias:
      description: "Ignoring failures"
      detection:
        - Track failure analysis
        - Alert if failures not analyzed
```

---

## Quality Metrics

```yaml
QualityMetrics:
  # How Meta Brain measures Core Brain quality
  
  DECISION_QUALITY:
    - Accuracy: "Did the decision achieve the goal?"
    - Efficiency: "Was the decision made efficiently?"
    - Consistency: "Are similar decisions made similarly?"
    - Appropriateness: "Was the decision appropriate for context?"
    
  PLANNING_QUALITY:
    - Completeness: "Are all requirements addressed?"
    - Accuracy: "Are estimates accurate?"
    - Realism: "Are plans realistic?"
    
  EXECUTION_QUALITY:
    - Success Rate: "What % of executions succeed?"
    - Quality: "What's the quality of outputs?"
    - Efficiency: "How efficiently are resources used?"
```

---

## Meta Brain Reports

```yaml
MetaBrainReports:
  # Reports generated by Meta Brain
  
  DAILY_REPORT:
    - Decisions made today
    - Success rate
    - Quality metrics
    - Anomalies detected
    
  WEEKLY_REPORT:
    - Trends in decision quality
    - Bias patterns
    - Policy effectiveness
    - Improvement recommendations
    
  MONTHLY_REPORT:
    - Core Brain performance summary
    - Major decisions review
    - Bias analysis
    - ADR recommendations
    
  INCIDENT_REPORT:
    - Triggered when anomaly detected
    - Details of issue
    - Recommendation
```

---

## Example: Bias Detection

```yaml
Situation:
  Core Brain always selects OpenHands for code generation
  
Detection:
  Meta Brain observes:
    - 95% of code generation uses OpenHands
    - 5% uses other providers
    - No clear reason for preference
    
Bias Alert:
  type: "PROVIDER_BIAS"
  description: "Core Brain shows strong preference for OpenHands"
  evidence:
    - "95% OpenHands usage for code generation"
    - "No recent evaluation of alternatives"
  recommendation:
    - "Consider benchmarking alternatives"
    - "Evaluate if preference is data-driven"
    
Response:
  Core Brain receives suggestion (NOT command):
  "Meta Brain suggests: Consider testing Cline for small tasks
   Evidence: Cline has 90% success rate for simple code generation"
```

---

## Meta Brain vs Core Brain

```yaml
Comparison:

CORE BRAIN:
  Role: OPERATIONAL
  Makes: Decisions
  Controls: Everything
  Direct: Commands
  
META BRAIN:
  Role: AUDIT/ADVISORY
  Makes: Suggestions
  Controls: Nothing
  Indirect: Recommendations

Boundary:
  ┌─────────────────────────────────────────────────────────┐
  │  Meta Brain: "I suggest X because Y"                     │
  │  Core Brain: "I decide to follow suggestion" OR "No"       │
  └─────────────────────────────────────────────────────────┘
  
  Meta Brain can suggest.
  Core Brain decides.
```

---

## Meta Brain Implementation

```python
class MetaBrain:
    """
    Brain About The Brain.
    """
    
    async def audit_decision(self, decision: Decision):
        """
        Audit a Core Brain decision.
        """
        
        # Record decision
        await self.record_decision(decision)
        
        # Analyze decision
        analysis = await self.analyze_decision(decision)
        
        # Check for biases
        biases = await self.detect_biases(decision)
        
        if biases:
            await self.alert_bias(biases)
            
        # Check quality
        quality = await self.assess_quality(decision)
        
        if quality < THRESHOLD:
            await self.alert_quality_issue(quality)
            
        # Update reports
        await self.update_reports(decision, analysis)
        
    async def generate_suggestions(self) -> list[Suggestion]:
        """
        Generate suggestions for Core Brain.
        """
        
        suggestions = []
        
        # Analyze trends
        trends = await self.analyze_trends()
        
        # Policy improvements
        if trends.show_policy_gaps():
            suggestions.append(
                Suggestion(
                    type="POLICY",
                    description="Consider updating X policy",
                    evidence=trends.evidence
                )
            )
            
        # ADR recommendations
        if trends.show_systematic_issues():
            suggestions.append(
                Suggestion(
                    type="ADR",
                    description="Consider new ADR for X",
                    evidence=trends.evidence
                )
            )
            
        return suggestions
```

---

## Meta Brain Constraints

```yaml
Constraints:
  # What Meta Brain CANNOT do
  
  CANNOT_COMMAND:
    - "Select this provider"
    - "Use this strategy"
    - "Ignore that warning"
    - "Change this policy"
    
  CANNOT_OVERRIDE:
    - Core Brain decisions
    - Provider selections
    - Execution flows
    
  CANNOT_ACCESS:
    - Direct provider APIs
    - Execution environments
    - User data (except for auditing)
    
  CANNOT_MODIFY:
    - Core Brain logic
    - Provider configurations
    - User policies
```

---

## Governance Integration

```yaml
GovernanceIntegration:
  # Meta Brain reports to governance
  
  reports_to:
    - Organization Governance
    - System Administrators
    - Compliance Officers
    
  report_types:
    - Decision audits
    - Bias alerts
    - Quality reports
    - ADR recommendations
    
  escalation:
    - Critical biases: Immediate alert
    - Quality degradation: Weekly review
    - Policy gaps: Monthly recommendations
```

---

## Related Documents

- [Platform-Governance.md](./02-Platform-Governance.md)
- [ADR-028: Everything is Resource](./04-ADRs/01-ADR-028-Everything-is-Resource.md)
