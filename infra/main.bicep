// Azure Container Apps hosting for the Insurance API + MCP server (used by `azd up`).
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment; used to name resources.')
param environmentName string

@minLength(1)
@description('Azure region for all resources.')
param location string

@secure()
@description('Optional API key. When set, clients must send it in the x-api-key header.')
param apiKey string = ''

@description('Existing Foundry resource that hosts the voice agent (for the public voice demo). Leave empty to skip.')
param foundryResourceName string = ''

@description('Resource group of the Foundry resource.')
param foundryResourceGroup string = ''

@description('Foundry project that contains the agent.')
param foundryProjectName string = ''

@description('Name of the Foundry agent the voice demo talks to.')
param voiceAgentName string = ''

var voiceEnabled = !empty(foundryResourceName) && !empty(foundryResourceGroup) && !empty(foundryProjectName) && !empty(voiceAgentName)

var tags = { 'azd-env-name': environmentName }
var token = toLower(uniqueString(subscription().id, environmentName, location))

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module resources 'resources.bicep' = {
  name: 'resources'
  scope: rg
  params: {
    location: location
    tags: tags
    token: token
    apiKey: apiKey
    voiceEndpoint: voiceEnabled ? 'https://${foundryResourceName}.services.ai.azure.com' : ''
    voiceProjectName: foundryProjectName
    voiceAgentName: voiceAgentName
  }
}

module foundryAccess 'foundry-access.bicep' = if (voiceEnabled) {
  name: 'foundry-access'
  scope: resourceGroup(voiceEnabled ? foundryResourceGroup : rg.name)
  params: {
    foundryResourceName: foundryResourceName
    principalId: resources.outputs.identityPrincipalId
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.registryLoginServer
output SERVICE_API_NAME string = resources.outputs.appName
output APPLICATIONINSIGHTS_NAME string = resources.outputs.appInsightsName
output SERVICE_API_URI string = resources.outputs.appUri
output MCP_ENDPOINT string = '${resources.outputs.appUri}/mcp'
output VOICE_DEMO_URL string = voiceEnabled ? resources.outputs.appUri : ''
