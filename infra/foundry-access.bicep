// Lets the container app's managed identity open Voice Live sessions with the Foundry agent.
// Deployed into the resource group that holds the existing Foundry resource.
param foundryResourceName string
param principalId string

resource foundry 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryResourceName
}

var roles = {
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'            // invoke Foundry agents
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'  // Voice Live speech + avatar
}

resource assignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for role in items(roles): {
  name: guid(foundry.id, principalId, role.value)
  scope: foundry
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.value)
  }
}]
