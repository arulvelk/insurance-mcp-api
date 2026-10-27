param location string
param tags object
param token string
@secure()
param apiKey string
param voiceEndpoint string = ''
param voiceProjectName string = ''
param voiceAgentName string = ''

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${token}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${token}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${token}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${token}'
  location: location
  tags: tags
}

// AcrPull for the app's managed identity
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, identity.id, 'acrpull')
  scope: registry
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

var appInsightsSecret = [ { name: 'appinsights-connection-string', value: appInsights.properties.ConnectionString } ]
var appInsightsEnv = [ { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' } ]
var secrets = concat(appInsightsSecret, empty(apiKey) ? [] : [ { name: 'api-key', value: apiKey } ])
var identityEnv = [ { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId } ]
var voiceEnv = empty(voiceEndpoint) ? [] : [
  { name: 'VOICE_ENDPOINT', value: voiceEndpoint }
  { name: 'VOICE_PROJECT_NAME', value: voiceProjectName }
  { name: 'VOICE_AGENT_NAME', value: voiceAgentName }
]
var envVars = concat(appInsightsEnv, identityEnv, voiceEnv, empty(apiKey) ? [] : [ { name: 'API_KEY', secretRef: 'api-key' } ])

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-insurance-${take(token, 8)}'
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [ acrPull ]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        { server: registry.properties.loginServer, identity: identity.id }
      ]
      secrets: secrets
    }
    template: {
      containers: [
        {
          name: 'api'
          // Placeholder; `azd deploy` replaces it with the image built from this repo.
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          env: envVars
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
      // The demo store is in memory, so keep one replica to keep newly filed claims consistent.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output registryLoginServer string = registry.properties.loginServer
output appName string = app.name
output identityPrincipalId string = identity.properties.principalId
output appInsightsName string = appInsights.name
output appUri string = 'https://${app.properties.configuration.ingress.fqdn}'
