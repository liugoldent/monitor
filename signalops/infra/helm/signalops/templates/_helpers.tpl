{{- define "signalops.name" -}}
signalops
{{- end }}

{{- define "signalops.labels" -}}
app.kubernetes.io/name: {{ include "signalops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
