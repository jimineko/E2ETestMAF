@description('Azure region for the workload resources.')
param location string = resourceGroup().location

@description('Globally unique lowercase prefix.')
@minLength(3)
@maxLength(12)
param prefix string

@description('Existing Azure OpenAI resource ID used for RBAC.')
param azureOpenAIResourceId string

@description('Azure OpenAI endpoint.')
param azureOpenAIEndpoint string

@description('Azure OpenAI deployment name.')
param azureOpenAIDeployment string

@description('Web application URL to test.')
param targetUrl string

@description('E2E testing objective passed to the scheduled container.')
param objective string = 'Validate the critical user journey and report regressions.'

@description('UTC time used by the systemd timer.')
param timerOnCalendar string = '*-*-* 18:00:00 UTC'

@description('Container image tag deployed to the new ACR.')
param imageTag string = 'latest'

@description('Globally unique storage account name.')
@minLength(3)
@maxLength(24)
param storageAccountName string

@description('SSH public key for break-glass VM administration. The VM has no public IP.')
param sshPublicKey string

@description('KVM-capable x64 VM size. Dsv5 Intel sizes support nested virtualization.')
param vmSize string = 'Standard_D2s_v5'

@description('Administrative username for the private VM.')
param adminUsername string = 'azureuser'

var identityName = '${prefix}-e2e-id'
var vmName = '${prefix}-e2e-vm'
var nicName = '${prefix}-e2e-nic'
var vnetName = '${prefix}-e2e-vnet'
var natName = '${prefix}-e2e-nat'
var natPublicIpName = '${prefix}-e2e-nat-pip'
var acrName = toLower(replace('${prefix}qaacr', '-', ''))
var workspaceName = '${prefix}-e2e-law'
var appInsightsName = '${prefix}-e2e-ai'
var azureOpenAIIdParts = split(azureOpenAIResourceId, '/')
var imageName = '${acr.properties.loginServer}/maf-playwright-e2e:${imageTag}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource artifactContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'e2e-artifacts'
  properties: {
    publicAccess: 'None'
  }
}

resource natPublicIp 'Microsoft.Network/publicIPAddresses@2024-05-01' = {
  name: natPublicIpName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource natGateway 'Microsoft.Network/natGateways@2024-05-01' = {
  name: natName
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    idleTimeoutInMinutes: 10
    publicIpAddresses: [
      { id: natPublicIp.id }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: vnetName
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: ['10.42.0.0/16']
    }
    subnets: [
      {
        name: 'workload'
        properties: {
          addressPrefix: '10.42.1.0/24'
          natGateway: { id: natGateway.id }
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2024-05-01' = {
  name: nicName
  location: location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          privateIPAllocationMethod: 'Dynamic'
          subnet: {
            id: resourceId('Microsoft.Network/virtualNetworks/subnets', vnet.name, 'workload')
          }
        }
      }
    ]
  }
}

var cloudInit = format('''
#cloud-config
package_update: true
packages:
  - docker.io
  - curl
  - ca-certificates
write_files:
  - path: /etc/maf-e2e.env
    permissions: '0600'
    content: |
      AZURE_CLIENT_ID={0}
      MAF_E2E_MODEL_PROVIDER=azure_openai
      MAF_E2E_AZURE_OPENAI_ENDPOINT={1}
      MAF_E2E_AZURE_OPENAI_DEPLOYMENT={2}
      MAF_E2E_TARGET_URL={3}
      MAF_E2E_OBJECTIVE={4}
      MAF_E2E_BLOB_ACCOUNT_URL=https://{5}.blob.{6}
      MAF_E2E_BLOB_CONTAINER={7}
      MAF_E2E_APPLICATIONINSIGHTS_CONNECTION_STRING={8}
      MAF_E2E_CODEACT_MODE=required
      MAF_E2E_CODEACT_REQUIRE_KVM=true
      MAF_E2E_CODEACT_ALLOW_FILE_UPLOAD=false
      MAF_E2E_CODEACT_ALLOW_DESTRUCTIVE_ACTIONS=false
      MAF_E2E_PLAYWRIGHT_HEADLESS=true
      MAF_E2E_PLAYWRIGHT_ALLOWED_ORIGINS={3}
  - path: /etc/systemd/system/maf-e2e.service
    permissions: '0644'
    content: |
      [Unit]
      Description=MAF Hyperlight autonomous E2E testing
      After=docker.service network-online.target
      Requires=docker.service

      [Service]
      Type=oneshot
      ExecStartPre=/usr/bin/az login --identity --username {0}
      ExecStartPre=/usr/bin/az acr login --name {9}
      ExecStartPre=-/usr/bin/docker rm -f maf-e2e
      ExecStart=/usr/bin/docker run --rm --name maf-e2e --device=/dev/kvm:/dev/kvm --env-file /etc/maf-e2e.env -v /var/lib/maf-e2e/artifacts:/app/artifacts -v /var/lib/maf-e2e/checkpoints:/app/checkpoints {10}
      TimeoutStartSec=2h
  - path: /etc/systemd/system/maf-e2e.timer
    permissions: '0644'
    content: |
      [Unit]
      Description=Run MAF E2E testing on schedule

      [Timer]
      OnCalendar={11}
      Persistent=true
      RandomizedDelaySec=5m

      [Install]
      WantedBy=timers.target
runcmd:
  - [bash, -lc, 'curl -sL https://aka.ms/InstallAzureCLIDeb | bash']
  - [bash, -lc, 'mkdir -p /var/lib/maf-e2e/artifacts /var/lib/maf-e2e/checkpoints']
  - [bash, -lc, 'test -e /dev/kvm']
  - [systemctl, enable, --now, docker]
  - [systemctl, daemon-reload]
  - [systemctl, enable, --now, maf-e2e.timer]
''', identity.properties.clientId, azureOpenAIEndpoint, azureOpenAIDeployment, targetUrl, objective, storage.name, az.environment().suffixes.storage, artifactContainer.name, appInsights.properties.ConnectionString, acr.name, imageName, timerOnCalendar)

resource vm 'Microsoft.Compute/virtualMachines@2024-07-01' = {
  name: vmName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identity.id}': {}
    }
  }
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: 'ubuntu-24_04-lts'
        sku: 'server'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      customData: base64(cloudInit)
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        { id: nic.id }
      ]
    }
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'AcrPull')
  scope: acr
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d'
    )
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, 'StorageBlobDataContributor')
  scope: storage
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
    )
  }
}

module openAIRbac './openai-rbac.bicep' = {
  name: 'openai-rbac-${uniqueString(identity.id, azureOpenAIResourceId)}'
  scope: resourceGroup(azureOpenAIIdParts[2], azureOpenAIIdParts[4])
  params: {
    accountName: azureOpenAIIdParts[8]
    principalId: identity.properties.principalId
  }
}

output acrLoginServer string = acr.properties.loginServer
output vmResourceId string = vm.id
output managedIdentityClientId string = identity.properties.clientId
output applicationInsightsConnectionString string = appInsights.properties.ConnectionString
